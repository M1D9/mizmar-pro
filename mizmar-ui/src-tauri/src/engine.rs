use std::{
    collections::VecDeque,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};

use fs2::available_space;
use reqwest::Url;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::{ShellExt, process::CommandEvent};
use tokio::{fs, io::AsyncWriteExt};

const CPU_SIDECAR: &str = "binaries/mizmar-engine-cpu";
const CUDA_ENGINE_FILENAME: &str = "mizmar-engine-cuda-x86_64-pc-windows-msvc.exe";
const CUDA_ENGINE_MANIFEST_URL: &str = "https://github.com/M1D9/mizmar-pro/releases/download/v0.1.0/mizmar-engine-cuda-x86_64-pc-windows-msvc.manifest.json";
const CUDA_ENGINE_SHA256: &str = "6296dd6800480ca0b1cccc6b2ef057a1238e5244139281b821090b888a6c0d3d";
const MAX_MANIFEST_BYTES: u64 = 1024 * 1024;
const MAX_ENGINE_BYTES: u64 = 6 * 1024 * 1024 * 1024;
const MAX_PART_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const DISK_RESERVE_BYTES: u64 = 512 * 1024 * 1024;
const USER_AGENT: &str = "MizmarPro/0.1";

static TEMP_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Deserialize)]
struct CudaEngineManifest {
    filename: String,
    size: u64,
    sha256: String,
    parts: Vec<CudaEnginePart>,
}

#[derive(Debug, Deserialize)]
struct CudaEnginePart {
    name: String,
    size: u64,
}

#[derive(Clone, Serialize)]
struct DownloadProgress {
    downloaded: u64,
    total: u64,
}

#[derive(Clone, Serialize)]
struct EngineOutput {
    run_id: String,
    line: String,
}

#[derive(Serialize)]
pub struct CudaEngineStatus {
    configured: bool,
    installed: bool,
}

struct TemporaryFile(Option<PathBuf>);

impl Drop for TemporaryFile {
    fn drop(&mut self) {
        if let Some(path) = &self.0 {
            let _ = std::fs::remove_file(path);
        }
    }
}

fn release_configuration() -> Result<(Url, String), String> {
    let expected_hash = CUDA_ENGINE_SHA256.to_ascii_lowercase();
    if expected_hash.len() != 64 || !expected_hash.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("لم تُضبط بصمة SHA-256 لمحرك CUDA بعد.".to_string());
    }
    if CUDA_ENGINE_MANIFEST_URL.contains("OWNER/REPOSITORY")
        || CUDA_ENGINE_MANIFEST_URL.contains("/TAG/")
    {
        return Err("لم يُضبط رابط GitHub Release لمحرك CUDA بعد.".to_string());
    }

    let manifest_url = Url::parse(CUDA_ENGINE_MANIFEST_URL)
        .map_err(|error| format!("رابط بيان CUDA غير صالح: {error}"))?;
    if manifest_url.scheme() != "https"
        || manifest_url.host_str() != Some("github.com")
        || !manifest_url.path().contains("/releases/download/")
    {
        return Err("يجب أن يكون بيان CUDA على رابط HTTPS ثابت من GitHub Releases.".to_string());
    }
    Ok((manifest_url, expected_hash))
}

fn cuda_engine_path(app: &AppHandle) -> Result<PathBuf, String> {
    let (_, expected_hash) = release_configuration()?;
    let app_data_directory = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("تعذر تحديد مجلد بيانات التطبيق: {error}"))?;
    Ok(app_data_directory
        .join("engines")
        .join(format!("mizmar-engine-cuda-{}.exe", &expected_hash[..16])))
}

fn emit_download_progress(app: &AppHandle, downloaded: u64, total: u64) -> Result<(), String> {
    app.emit(
        "cuda-download-progress",
        DownloadProgress { downloaded, total },
    )
    .map_err(|error| format!("تعذر تحديث شريط تنزيل CUDA: {error}"))
}

fn hash_file(path: &Path) -> Result<String, String> {
    let mut file = File::open(path)
        .map_err(|error| format!("تعذر فتح محرك CUDA للتحقق من البصمة: {error}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let bytes_read = file
            .read(&mut buffer)
            .map_err(|error| format!("تعذر قراءة محرك CUDA للتحقق من البصمة: {error}"))?;
        if bytes_read == 0 {
            break;
        }
        hasher.update(&buffer[..bytes_read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

#[tauri::command]
pub fn cuda_engine_status(app: AppHandle) -> Result<CudaEngineStatus, String> {
    let configured = release_configuration().is_ok();
    let installed = if configured {
        cuda_engine_path(&app)?.is_file()
    } else {
        false
    };
    Ok(CudaEngineStatus {
        configured,
        installed,
    })
}

#[tauri::command]
pub async fn install_cuda_engine(app: AppHandle) -> Result<String, String> {
    let (manifest_url, expected_hash) = release_configuration()?;
    let engine_directory = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("تعذر تحديد مجلد بيانات التطبيق: {error}"))?
        .join("engines");
    fs::create_dir_all(&engine_directory)
        .await
        .map_err(|error| format!("تعذر إنشاء مجلد محرك CUDA: {error}"))?;

    let target_path = cuda_engine_path(&app)?;
    if target_path.is_file() {
        let existing_path = target_path.clone();
        let existing_hash = tauri::async_runtime::spawn_blocking(move || hash_file(&existing_path))
            .await
            .map_err(|error| format!("تعذر التحقق من المحرك المثبت: {error}"))??;
        if existing_hash == expected_hash {
            emit_download_progress(&app, 1, 1)?;
            return Ok(target_path.to_string_lossy().into_owned());
        }
    }

    let client = reqwest::Client::builder()
        .user_agent(USER_AGENT)
        .connect_timeout(Duration::from_secs(30))
        .timeout(Duration::from_secs(3 * 60 * 60))
        .build()
        .map_err(|error| format!("تعذر إعداد اتصال تنزيل CUDA: {error}"))?;
    let manifest_response = client
        .get(manifest_url.clone())
        .send()
        .await
        .map_err(|error| format!("تعذر الاتصال بإصدار CUDA: {error}"))?
        .error_for_status()
        .map_err(|error| format!("تعذر تنزيل بيان إصدار CUDA: {error}"))?;
    if manifest_response
        .content_length()
        .is_some_and(|length| length > MAX_MANIFEST_BYTES)
    {
        return Err("بيان إصدار CUDA أكبر من الحجم المسموح.".to_string());
    }
    let manifest_bytes = manifest_response
        .bytes()
        .await
        .map_err(|error| format!("تعذر قراءة بيان إصدار CUDA: {error}"))?;
    if manifest_bytes.len() as u64 > MAX_MANIFEST_BYTES {
        return Err("بيان إصدار CUDA أكبر من الحجم المسموح.".to_string());
    }
    let manifest: CudaEngineManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|error| format!("بيان CUDA غير صالح: {error}"))?;
    if manifest.filename != CUDA_ENGINE_FILENAME
        || manifest.sha256.to_ascii_lowercase() != expected_hash
    {
        return Err("اسم الملف أو بصمة البيان لا تطابقان الإصدار الموثوق داخل التطبيق.".to_string());
    }
    if manifest.size == 0 || manifest.size > MAX_ENGINE_BYTES || manifest.parts.is_empty() {
        return Err("حجم محرك CUDA أو قائمة أجزائه غير صالحة.".to_string());
    }
    if manifest.parts.len() > 32 {
        return Err("عدد أجزاء محرك CUDA يتجاوز الحد المسموح.".to_string());
    }

    let mut parts_size = 0_u64;
    for part in &manifest.parts {
        if part.size == 0
            || part.size >= MAX_PART_BYTES
            || part.name.is_empty()
            || part.name.contains("..")
            || !part
                .name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        {
            return Err("اسم أو حجم أحد أجزاء محرك CUDA غير صالح.".to_string());
        }
        parts_size = parts_size
            .checked_add(part.size)
            .ok_or_else(|| "تجاوز الحجم الإجمالي لأجزاء CUDA الحد العددي.".to_string())?;
    }
    if parts_size != manifest.size {
        return Err("مجموع أحجام أجزاء CUDA لا يطابق حجم المحرك في البيان.".to_string());
    }

    let available = available_space(&engine_directory)
        .map_err(|error| format!("تعذر التحقق من المساحة المتاحة على القرص: {error}"))?;
    let required_space = manifest
        .size
        .checked_add(DISK_RESERVE_BYTES)
        .ok_or_else(|| "حجم محرك CUDA يتجاوز المساحة القابلة للفحص.".to_string())?;
    if available < required_space {
        return Err(format!(
            "المساحة غير كافية لتنزيل CUDA: المتاح {:.2} GiB، والمطلوب {:.2} GiB مع مساحة احتياطية.",
            available as f64 / (1024_f64 * 1024_f64 * 1024_f64),
            required_space as f64 / (1024_f64 * 1024_f64 * 1024_f64)
        ));
    }

    let sequence = TEMP_FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temporary_path = engine_directory.join(format!(
        ".mizmar-engine-cuda-{}-{}-{}.part",
        &expected_hash[..16],
        std::process::id(),
        sequence
    ));
    let mut temporary_file = TemporaryFile(Some(temporary_path.clone()));
    let mut output_file = fs::File::create(&temporary_path)
        .await
        .map_err(|error| format!("تعذر إنشاء ملف تنزيل CUDA المؤقت: {error}"))?;
    let mut hasher = Sha256::new();
    let mut downloaded = 0_u64;
    emit_download_progress(&app, 0, manifest.size)?;

    for part in &manifest.parts {
        let part_url = manifest_url
            .join(&part.name)
            .map_err(|error| format!("رابط جزء CUDA غير صالح: {error}"))?;
        let mut response = client
            .get(part_url)
            .send()
            .await
            .map_err(|error| format!("تعذر تنزيل الجزء {}: {error}", part.name))?
            .error_for_status()
            .map_err(|error| format!("فشل تنزيل الجزء {}: {error}", part.name))?;
        if response.content_length() != Some(part.size) {
            return Err(format!(
                "حجم الجزء {} لا يطابق الحجم المعلن في البيان.",
                part.name
            ));
        }

        let mut part_downloaded = 0_u64;
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|error| format!("انقطع تنزيل الجزء {}: {error}", part.name))?
        {
            part_downloaded = part_downloaded
                .checked_add(chunk.len() as u64)
                .ok_or_else(|| "تجاوز حجم تنزيل الجزء الحد العددي.".to_string())?;
            downloaded = downloaded
                .checked_add(chunk.len() as u64)
                .ok_or_else(|| "تجاوز حجم تنزيل CUDA الحد العددي.".to_string())?;
            if part_downloaded > part.size || downloaded > manifest.size {
                return Err("حجم البيانات المستلمة تجاوز الحجم المعلن.".to_string());
            }
            hasher.update(&chunk);
            output_file
                .write_all(&chunk)
                .await
                .map_err(|error| format!("تعذرت كتابة ملف CUDA المؤقت: {error}"))?;
            emit_download_progress(&app, downloaded, manifest.size)?;
        }
        if part_downloaded != part.size {
            return Err(format!("اكتمل تنزيل جزء {} بحجم غير مطابق.", part.name));
        }
    }

    if downloaded != manifest.size {
        return Err("حجم محرك CUDA المنزّل لا يطابق الحجم المعلن.".to_string());
    }
    output_file
        .flush()
        .await
        .map_err(|error| format!("تعذر إنهاء كتابة محرك CUDA: {error}"))?;
    output_file
        .sync_all()
        .await
        .map_err(|error| format!("تعذر تثبيت محرك CUDA على القرص: {error}"))?;
    drop(output_file);

    let actual_hash = format!("{:x}", hasher.finalize());
    if actual_hash != expected_hash {
        return Err("فشل التحقق من SHA-256 لمحرك CUDA؛ لم يتم تثبيته أو تشغيله.".to_string());
    }

    if target_path.exists() {
        fs::remove_file(&target_path)
            .await
            .map_err(|error| format!("تعذر استبدال نسخة CUDA القديمة: {error}"))?;
    }
    fs::rename(&temporary_path, &target_path)
        .await
        .map_err(|error| format!("تعذر تثبيت محرك CUDA بعد التحقق منه: {error}"))?;
    temporary_file.0 = None;
    emit_download_progress(&app, manifest.size, manifest.size)?;
    Ok(target_path.to_string_lossy().into_owned())
}

#[tauri::command]
pub async fn run_engine(
    app: AppHandle,
    config_path: String,
    run_id: String,
    use_cuda: bool,
) -> Result<(), String> {
    let config_file = PathBuf::from(&config_path);
    if !config_file.is_file() {
        return Err(format!("ملف إعدادات المهمة غير موجود: {config_path}"));
    }

    let app_data_directory = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("تعذر تحديد مجلد بيانات التطبيق: {error}"))?;
    let output_directory = app_data_directory.join("output");
    let resource_directory = app
        .path()
        .resource_dir()
        .map_err(|error| format!("تعذر تحديد مجلد موارد التطبيق: {error}"))?;
    std::fs::create_dir_all(&output_directory)
        .map_err(|error| format!("تعذر إنشاء مجلد نتائج الفيديو: {error}"))?;

    let cuda_path = if use_cuda {
        cuda_engine_path(&app).ok().filter(|path| path.is_file())
    } else {
        None
    };
    let engine_mode = if cuda_path.is_some() { "cuda" } else { "cpu" };
    let mut command = if let Some(path) = cuda_path {
        app.shell().command(path)
    } else {
        app.shell()
            .sidecar(CPU_SIDECAR)
            .map_err(|error| format!("تعذر تحديد محرك CPU المضمن: {error}"))?
    };
    command = command
        .arg(&config_path)
        .env("MIZMAR_FFMPEG_DIR", resource_directory.join("ffmpeg"))
        .env("MIZMAR_OUTPUT_DIR", output_directory)
        .env("MIZMAR_ENGINE_MODE", engine_mode)
        .set_process_group(true)
        .kill_on_drop(true);

    let (mut receiver, _child) = command
        .spawn()
        .map_err(|error| format!("تعذر تشغيل محرك {engine_mode}: {error}"))?;
    let mut exit_code = None;
    let mut stderr_tail = VecDeque::new();

    while let Some(event) = receiver.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                app.emit(
                    "engine-stdout",
                    EngineOutput {
                        run_id: run_id.clone(),
                        line: String::from_utf8_lossy(&line).trim_end().to_string(),
                    },
                )
                .map_err(|error| format!("تعذر إرسال سجل المحرك إلى الواجهة: {error}"))?;
            }
            CommandEvent::Stderr(line) => {
                let line = String::from_utf8_lossy(&line).trim_end().to_string();
                if !line.is_empty() {
                    stderr_tail.push_back(line.clone());
                    if stderr_tail.len() > 20 {
                        stderr_tail.pop_front();
                    }
                    app.emit(
                        "engine-stderr",
                        EngineOutput {
                            run_id: run_id.clone(),
                            line,
                        },
                    )
                    .map_err(|error| format!("تعذر إرسال خطأ المحرك إلى الواجهة: {error}"))?;
                }
            }
            CommandEvent::Terminated(payload) => exit_code = payload.code,
            CommandEvent::Error(error) => {
                return Err(format!("خطأ أثناء تشغيل محرك {engine_mode}: {error}"));
            }
            _ => {}
        }
    }

    if exit_code == Some(0) {
        Ok(())
    } else {
        let details = stderr_tail.into_iter().collect::<Vec<_>>().join("\n");
        Err(if details.is_empty() {
            format!("انتهى محرك {engine_mode} برمز خروج غير ناجح: {exit_code:?}")
        } else {
            details
        })
    }
}
