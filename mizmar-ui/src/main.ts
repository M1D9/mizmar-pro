import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { appLocalDataDir, join, videoDir } from '@tauri-apps/api/path';
import { listen } from '@tauri-apps/api/event';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { open } from '@tauri-apps/plugin-dialog';
import { mkdir, writeTextFile } from '@tauri-apps/plugin-fs';

type ToolType = 'upscale' | 'motion_blur' | 'lossless' | 'convert';
type PageName = 'dashboard' | ToolType | 'queue' | 'help' | 'settings';
type QueueStatus = 'queued' | 'running' | 'completed' | 'failed';
type UiState = 'idle' | 'loading' | 'ready' | 'queued' | 'running' | 'success' | 'error';
type JobConfig = Record<string, string | number | boolean | null>;
type VideoInfo = {
    file_name: string;
    file_size_bytes: number;
    width: number | null;
    height: number | null;
    fps: number | null;
    duration_seconds: number | null;
    codec: string | null;
};
type QueueJob = {
    id: string;
    label: string;
    toolType: ToolType;
    configPath: string;
    status: QueueStatus;
    message: string;
};
type EngineOutput = { run_id: string; line: string };
type CudaDownloadProgress = { downloaded: number; total: number };
type CudaEngineStatus = { configured: boolean; installed: boolean };

const toolInfo: Record<ToolType, { title: string; inputHint: string }> = {
    upscale: { title: 'رفع الجودة', inputHint: 'اختر فيديو لرفع الجودة.' },
    motion_blur: { title: 'تمويه الحركة', inputHint: 'اختر فيديو لتمويه الحركة.' },
    lossless: { title: 'الرفع بدون ضغط', inputHint: 'اختر فيديو لمعالجة Lossless.' },
    convert: { title: 'تحويل الصيغ', inputHint: 'اختر فيديو للتحويل.' }
};

const cudaEnabledStorageKey = 'mizmar.cuda.enabled';
const selectedPaths: Record<ToolType, string> = {
    upscale: '',
    motion_blur: '',
    lossless: '',
    convert: ''
};
const probeRequests: Record<ToolType, number> = {
    upscale: 0,
    motion_blur: 0,
    lossless: 0,
    convert: 0
};
const thumbnailRequests: Record<ToolType, number> = {
    upscale: 0,
    motion_blur: 0,
    lossless: 0,
    convert: 0
};
const toolReady: Record<ToolType, boolean> = {
    upscale: false,
    motion_blur: false,
    lossless: false,
    convert: false
};
const queue: QueueJob[] = [];
let currentPage: PageName = 'dashboard';
let queueRunning = false;
let activeEngineRuns = 0;
let cudaEngineConfigured = false;
let cudaEngineInstalled = false;
let cudaDownloadInProgress = false;

function isToolType(value: string | undefined): value is ToolType {
    return value === 'upscale' || value === 'motion_blur' || value === 'lossless' || value === 'convert';
}

function showPage(page: PageName): void {
    currentPage = page;
    document.querySelectorAll<HTMLElement>('.page-content').forEach((section) => {
        section.classList.toggle('active', section.id === `page-${page}`);
    });
    document.querySelectorAll<HTMLButtonElement>('.nav-btn').forEach((button) => {
        button.classList.toggle('active', button.dataset.page === page);
    });
}

function setStatus(toolType: ToolType, message: string, state: UiState = 'idle'): void {
    const status = document.querySelector<HTMLElement>(`[data-status-for="${toolType}"]`);
    if (status) {
        status.textContent = message;
        status.dataset.state = state;
    }
}

function setToolReady(toolType: ToolType, ready: boolean): void {
    toolReady[toolType] = ready;
    const button = document.querySelector<HTMLButtonElement>(`[data-start-tool="${toolType}"]`);
    if (!button) return;
    button.disabled = !ready;
    button.classList.toggle('is-ready', ready);
}

function normalizeWindowsPath(path: string): string {
    return path.replace(/[\\/]+/g, '\\').replace(/\\+$/, '').toLocaleLowerCase();
}

function isWithinDirectory(path: string, directory: string): boolean {
    const normalizedPath = normalizeWindowsPath(path);
    const normalizedDirectory = normalizeWindowsPath(directory);
    return normalizedPath === normalizedDirectory || normalizedPath.startsWith(`${normalizedDirectory}\\`);
}

function updateThumbnailFallback(toolType: ToolType, message: string): void {
    const placeholder = document.querySelector<HTMLElement>(`[data-thumbnail-placeholder="${toolType}"]`);
    const messageElement = document.querySelector<HTMLElement>(`[data-thumbnail-message="${toolType}"]`);
    const status = document.querySelector<HTMLElement>(`[data-preview-state="${toolType}"]`);
    const video = document.querySelector<HTMLVideoElement>(`[data-video-thumbnail="${toolType}"]`);
    if (placeholder) placeholder.hidden = false;
    if (messageElement) messageElement.textContent = message;
    if (status) status.textContent = 'معاينة رمزية';
    video?.classList.remove('is-visible');
}

async function loadVideoThumbnail(toolType: ToolType, inputPath: string): Promise<void> {
    const requestId = ++thumbnailRequests[toolType];
    const video = document.querySelector<HTMLVideoElement>(`[data-video-thumbnail="${toolType}"]`);
    const placeholder = document.querySelector<HTMLElement>(`[data-thumbnail-placeholder="${toolType}"]`);
    const message = document.querySelector<HTMLElement>(`[data-thumbnail-message="${toolType}"]`);
    const previewState = document.querySelector<HTMLElement>(`[data-preview-state="${toolType}"]`);
    if (!video || !placeholder || !message) return;

    const showPreviewFrame = () => {
        if (requestId !== thumbnailRequests[toolType]) return;
        video.classList.add('is-visible');
        placeholder.hidden = true;
        if (previewState) previewState.textContent = 'معاينة جاهزة';
    };

    video.pause();
    video.onerror = null;
    video.onloadeddata = null;
    video.onloadedmetadata = null;
    video.onseeked = null;
    video.removeAttribute('src');
    video.load();
    video.classList.remove('is-visible');
    placeholder.hidden = false;
    message.textContent = 'جاري تجهيز المعاينة';
    if (previewState) previewState.textContent = 'جاري التحليل';

    try {
        const allowedDirectory = await videoDir();
        if (requestId !== thumbnailRequests[toolType]) return;
        if (!isWithinDirectory(inputPath, allowedDirectory)) {
            updateThumbnailFallback(toolType, 'المعاينة الفعلية متاحة داخل مجلد Videos فقط');
            return;
        }

        video.onerror = () => {
            if (requestId === thumbnailRequests[toolType]) {
                updateThumbnailFallback(toolType, 'تعذرت معاينة هذا الترميز');
            }
        };
        video.onloadeddata = showPreviewFrame;
        video.onloadedmetadata = () => {
            if (requestId !== thumbnailRequests[toolType]) return;
            const seekTime = Number.isFinite(video.duration) && video.duration > 0.1
                ? Math.min(0.25, video.duration / 2)
                : 0;
            try {
                video.currentTime = seekTime;
            } catch (error) {
                console.warn('تعذر تحديد إطار المعاينة؛ سيُستخدم الإطار الأول:', error);
                showPreviewFrame();
            }
        };
        video.onseeked = showPreviewFrame;
        video.src = convertFileSrc(inputPath);
        video.load();
    } catch (error) {
        if (requestId !== thumbnailRequests[toolType]) return;
        updateThumbnailFallback(toolType, 'المعاينة غير متاحة لهذا الملف');
        console.warn(`تعذر تجهيز معاينة الفيديو (${toolType}):`, error);
    }
}

function clearVideoThumbnail(toolType: ToolType): void {
    thumbnailRequests[toolType] += 1;
    const preview = document.querySelector<HTMLElement>(`[data-video-preview="${toolType}"]`);
    const video = document.querySelector<HTMLVideoElement>(`[data-video-thumbnail="${toolType}"]`);
    const dropZone = document.querySelector<HTMLElement>(`[data-drop-zone="${toolType}"]`);
    if (preview) preview.hidden = true;
    dropZone?.classList.remove('has-video');
    if (video) {
        video.onerror = null;
        video.onloadeddata = null;
        video.onloadedmetadata = null;
        video.onseeked = null;
        video.pause();
        video.removeAttribute('src');
        video.load();
    }
}

function setSelectedVideo(toolType: ToolType, path: string): void {
    const supportedExtension = toolType === 'lossless'
        ? /\.mp4$/i.test(path)
        : /\.(mp4|mov|mkv|avi)$/i.test(path);
    if (!supportedExtension) {
        selectedPaths[toolType] = '';
        probeRequests[toolType] += 1;
        setToolReady(toolType, false);
        clearVideoThumbnail(toolType);
        const label = document.querySelector<HTMLElement>(`[data-selected-file="${toolType}"]`);
        const infoBar = document.querySelector<HTMLElement>(`[data-video-info-for="${toolType}"]`);
        if (infoBar) infoBar.hidden = true;
        if (label) label.textContent = toolType === 'lossless'
            ? 'ترقيع Lossless يدعم ملفات MP4 فقط.'
            : 'صيغة الفيديو غير مدعومة.';
        setStatus(toolType, toolType === 'lossless'
            ? 'اختر ملف MP4 بترميز H.264 أو H.265.'
            : 'يرجى اختيار فيديو بصيغة MP4 أو MOV أو MKV أو AVI.', 'error');
        return;
    }

    selectedPaths[toolType] = path;
    setToolReady(toolType, false);
    const fileName = path.split(/[\\/]/).pop() || path;
    const label = document.querySelector<HTMLElement>(`[data-selected-file="${toolType}"]`);
    if (label) label.textContent = fileName;
    const dropZone = document.querySelector<HTMLElement>(`[data-drop-zone="${toolType}"]`);
    const preview = document.querySelector<HTMLElement>(`[data-video-preview="${toolType}"]`);
    const previewName = document.querySelector<HTMLElement>(`[data-preview-name="${toolType}"]`);
    const previewSize = document.querySelector<HTMLElement>(`[data-preview-size="${toolType}"]`);
    const previewFormat = document.querySelector<HTMLElement>(`[data-thumbnail-format="${toolType}"]`);
    if (dropZone) dropZone.classList.add('has-video');
    if (preview) preview.hidden = false;
    if (previewName) previewName.textContent = fileName;
    if (previewSize) previewSize.textContent = 'جاري قراءة الحجم…';
    if (previewFormat) previewFormat.textContent = fileName.split('.').pop()?.toUpperCase() || 'VIDEO';
    setStatus(toolType, 'جاري تجهيز الفيديو...', 'loading');
    void loadVideoThumbnail(toolType, path);
    void loadVideoInfo(toolType, path);
}

async function chooseVideo(toolType: ToolType): Promise<void> {
    try {
        const selected = await open({
            multiple: false,
            filters: [{
                name: 'Video',
                extensions: toolType === 'lossless' ? ['mp4'] : ['mp4', 'mov', 'mkv', 'avi']
            }]
        });
        if (typeof selected === 'string') setSelectedVideo(toolType, selected);
    } catch (error) {
        console.error('تعذر فتح نافذة اختيار الفيديو:', error);
        setStatus(toolType, 'تعذر فتح نافذة اختيار الفيديو.', 'error');
    }
}

function buildConfig(toolType: ToolType): JobConfig {
    const config: JobConfig = {
        job_type: 'process',
        tool_type: toolType,
        input_path: selectedPaths[toolType]
    };

    if (toolType === 'upscale') {
        const resolution = document.querySelector<HTMLElement>('.res-card.active')?.dataset.resolution;
        if (resolution !== '1080p' && resolution !== '2k' && resolution !== '4k' && resolution !== 'original') {
            throw new Error('اختر دقة إخراج مدعومة.');
        }
        config.resolution = resolution;
        config.codec = (document.getElementById('codec-select') as HTMLSelectElement).value;
        const fps = (document.getElementById('fps-select') as HTMLSelectElement).value;
        config.fps = fps === 'original' ? fps : Number(fps);
        config.ai_model = (document.getElementById('ai-model-select') as HTMLSelectElement).value;
        config.denoise = (document.getElementById('denoise-toggle') as HTMLInputElement).checked;
    } else if (toolType === 'motion_blur') {
        config.blur_amount = Number((document.getElementById('blur-amount-select') as HTMLInputElement).value);
        config.blur_style = (document.getElementById('blur-style-select') as HTMLSelectElement).value;
        config.interpolation_engine = (document.getElementById('interpolation-engine-select') as HTMLSelectElement).value;
    } else if (toolType === 'lossless') {
        config.multiplier = Number((document.getElementById('lossless-multiplier-select') as HTMLSelectElement).value);
    } else {
        config.target_format = (document.getElementById('convert-format-select') as HTMLSelectElement).value;
        config.quality = (document.getElementById('convert-quality-select') as HTMLSelectElement).value;
    }

    return config;
}

async function saveJobConfig(jobId: string, config: JobConfig): Promise<string> {
    const dataDirectory = await appLocalDataDir();
    const jobDirectory = await join(dataDirectory, 'jobs', jobId);
    await mkdir(jobDirectory, { recursive: true });
    const configPath = await join(jobDirectory, 'config.json');
    await writeTextFile(configPath, JSON.stringify(config, null, 2));
    return configPath;
}

function isOptionalNumber(value: unknown): value is number | null {
    return value === null || (typeof value === 'number' && Number.isFinite(value));
}

function isVideoInfo(value: unknown): value is VideoInfo {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
    const info = value as Record<string, unknown>;
    return typeof info.file_name === 'string'
        && typeof info.file_size_bytes === 'number'
        && Number.isFinite(info.file_size_bytes)
        && isOptionalNumber(info.width)
        && isOptionalNumber(info.height)
        && isOptionalNumber(info.fps)
        && isOptionalNumber(info.duration_seconds)
        && (typeof info.codec === 'string' || info.codec === null);
}

function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let size = bytes / 1024;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024;
        unitIndex += 1;
    }
    return `${size.toFixed(1)} ${units[unitIndex]}`;
}

function formatDuration(seconds: number | null): string {
    if (seconds === null || seconds < 0) return 'غير متاح';
    const totalSeconds = Math.floor(seconds);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const remainingSeconds = totalSeconds % 60;
    return hours > 0
        ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
        : `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
}

function displayNumber(value: number | null, digits = 2): string {
    if (value === null) return 'غير متاح';
    return Number(value.toFixed(digits)).toString();
}

function renderVideoInfo(toolType: ToolType, info: VideoInfo): void {
    const infoBar = document.querySelector<HTMLElement>(`[data-video-info-for="${toolType}"]`);
    if (!infoBar) return;
    infoBar.hidden = false;

    const errorLabel = infoBar.querySelector<HTMLElement>(`[data-video-info-error="${toolType}"]`);
    if (errorLabel) {
        errorLabel.hidden = true;
        errorLabel.textContent = '';
    }
    const resolution = info.width !== null && info.height !== null
        ? `${info.width} × ${info.height}`
        : 'غير متاح';
    const values: Record<string, string> = {
        file_name: info.file_name,
        file_size: formatFileSize(info.file_size_bytes),
        resolution,
        fps: info.fps === null ? 'غير متاح' : `${displayNumber(info.fps)} FPS`,
        duration: formatDuration(info.duration_seconds),
        codec: info.codec || 'غير متاح'
    };
    for (const [field, text] of Object.entries(values)) {
        const element = infoBar.querySelector<HTMLElement>(`[data-video-info-field="${field}"]`);
        if (element) element.textContent = text;
    }
    const previewName = document.querySelector<HTMLElement>(`[data-preview-name="${toolType}"]`);
    const previewSize = document.querySelector<HTMLElement>(`[data-preview-size="${toolType}"]`);
    if (previewName) previewName.textContent = info.file_name;
    if (previewSize) previewSize.textContent = formatFileSize(info.file_size_bytes);
}

async function loadVideoInfo(toolType: ToolType, inputPath: string): Promise<void> {
    const requestId = ++probeRequests[toolType];
    const infoBar = document.querySelector<HTMLElement>(`[data-video-info-for="${toolType}"]`);
    if (infoBar) {
        infoBar.hidden = false;
        const errorLabel = infoBar.querySelector<HTMLElement>(`[data-video-info-error="${toolType}"]`);
        if (errorLabel) {
            errorLabel.hidden = true;
            errorLabel.textContent = '';
        }
        infoBar.querySelectorAll<HTMLElement>('[data-video-info-field]').forEach((field) => {
            field.textContent = '…';
        });
    }
    setToolReady(toolType, false);
    setStatus(toolType, 'جاري قراءة معلومات الفيديو عبر FFprobe...', 'loading');

    try {
        const jobId = `probe-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const configPath = await saveJobConfig(jobId, {
            job_type: 'probe',
            input_path: inputPath
        });
        let responseJson = '';
        await runPythonCommand(configPath, (line) => {
            if (line.startsWith('VIDEO_INFO_JSON:')) {
                responseJson = line.slice('VIDEO_INFO_JSON:'.length);
            } else if (line.trim()) {
                console.info(`[Mizmar] ${line}`);
            }
        });

        if (requestId !== probeRequests[toolType] || inputPath !== selectedPaths[toolType]) return;
        if (!responseJson) throw new Error('لم يُرجع محرك Python معلومات الفيديو.');
        const parsed: unknown = JSON.parse(responseJson);
        if (!isVideoInfo(parsed)) throw new Error('تنسيق معلومات الفيديو المُرجع غير صالح.');
        renderVideoInfo(toolType, parsed);
        if (toolType === 'lossless') {
            const codec = parsed.codec?.toLowerCase();
            const losslessSupported = codec === 'h264' || codec === 'hevc';
            const startButton = document.querySelector<HTMLButtonElement>('[data-start-tool="lossless"]')!;
            startButton.disabled = !losslessSupported;
            startButton.textContent = codec === 'hevc' ? 'إعادة تغليف H.265' : 'تخطي الضغط';
            const multiplierSelect = document.getElementById('lossless-multiplier-select') as HTMLSelectElement;
            const methodNote = document.getElementById('lossless-method-note');
            multiplierSelect.disabled = codec === 'hevc' || !losslessSupported;
            if (!losslessSupported) {
                setToolReady(toolType, false);
                const previewState = document.querySelector<HTMLElement>(`[data-preview-state="${toolType}"]`);
                if (previewState) previewState.textContent = 'ترميز غير مدعوم';
                const message = 'يدعم Lossless ملفات MP4 بترميز H.264 أو H.265 فقط.';
                const errorLabel = infoBar?.querySelector<HTMLElement>(`[data-video-info-error="${toolType}"]`);
                if (errorLabel) {
                    errorLabel.textContent = message;
                    errorLabel.hidden = false;
                }
                setStatus(toolType, message, 'error');
                return;
            }
            if (methodNote) {
                methodNote.textContent = codec === 'hevc'
                    ? 'H.265: إعادة تغليف MP4 بنسخ المسارات مع وسم hvc1 دون إعادة ترميز. لا ينطبق معامل التضخيم على H.265 ولا يُضمن منع إعادة ضغط المنصة.'
                    : 'H.264: ترقيع عبر tiktok-quality بالمعامل المختار، دون إعادة ترميز. لا يُضمن منع إعادة ضغط المنصة.';
            }
        }
        setToolReady(toolType, true);
        setStatus(toolType, 'الفيديو جاهز للمعالجة.', 'ready');
    } catch (error) {
        if (requestId !== probeRequests[toolType] || inputPath !== selectedPaths[toolType]) return;
        const message = error instanceof Error ? error.message : String(error);
        infoBar?.querySelectorAll<HTMLElement>('[data-video-info-field]').forEach((field) => {
            field.textContent = 'غير متاح';
        });
        const errorLabel = infoBar?.querySelector<HTMLElement>(`[data-video-info-error="${toolType}"]`);
        if (errorLabel) {
            errorLabel.textContent = `تعذر جلب المعلومات: ${message}`;
            errorLabel.hidden = false;
        }
        setToolReady(toolType, false);
        const previewSize = document.querySelector<HTMLElement>(`[data-preview-size="${toolType}"]`);
        const previewState = document.querySelector<HTMLElement>(`[data-preview-state="${toolType}"]`);
        if (previewSize) previewSize.textContent = 'الحجم غير متاح';
        if (previewState) previewState.textContent = 'تعذر التحليل';
        setStatus(toolType, `تعذر قراءة معلومات الفيديو: ${message}`, 'error');
        console.error(`تعذر قراءة معلومات الفيديو (${toolType}):`, error);
    }
}

async function runPythonCommand(configPath: string, onStdoutLine: (line: string) => void): Promise<void> {
    const runId = `run-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    let stderrOutput = '';
    let unlistenStdout: (() => void) | undefined;
    let unlistenStderr: (() => void) | undefined;
    activeEngineRuns += 1;
    updateCudaControls();
    try {
        unlistenStdout = await listen<EngineOutput>('engine-stdout', (event) => {
            if (event.payload.run_id === runId) onStdoutLine(event.payload.line);
        });
        unlistenStderr = await listen<EngineOutput>('engine-stderr', (event) => {
            if (event.payload.run_id !== runId) return;
            console.error(event.payload.line);
            stderrOutput = `${stderrOutput}${event.payload.line}\n`.slice(-2000);
        });
        await invoke<void>('run_engine', {
            configPath,
            runId,
            useCuda: localStorage.getItem(cudaEnabledStorageKey) === 'true'
        });
    } catch (error) {
        const message = stderrOutput.trim() || (error instanceof Error ? error.message : String(error));
        throw new Error(message);
    } finally {
        unlistenStdout?.();
        unlistenStderr?.();
        activeEngineRuns -= 1;
        updateCudaControls();
    }
}

function updateCudaControls(): void {
    const toggle = document.getElementById('cuda-enabled-toggle') as HTMLInputElement | null;
    const downloadButton = document.getElementById('cuda-download-button') as HTMLButtonElement | null;
    if (toggle) {
        toggle.checked = cudaEngineInstalled && localStorage.getItem(cudaEnabledStorageKey) === 'true';
        toggle.disabled = !cudaEngineInstalled || cudaDownloadInProgress;
    }
    if (downloadButton) {
        downloadButton.disabled = !cudaEngineConfigured || cudaDownloadInProgress || activeEngineRuns > 0;
        downloadButton.textContent = cudaEngineInstalled
            ? 'إعادة تنزيل محرك CUDA'
            : 'تنزيل محرك CUDA وتفعيله';
    }
}

async function refreshCudaEngineStatus(): Promise<void> {
    const status = await invoke<CudaEngineStatus>('cuda_engine_status');
    cudaEngineConfigured = status.configured;
    cudaEngineInstalled = status.installed;
    updateCudaControls();

    const message = document.getElementById('cuda-engine-status');
    if (!message) return;
    if (!status.configured) {
        message.textContent = 'لم يُضبط رابط GitHub Release لمحرك CUDA بعد؛ التفعيل معطل حتى إدخاله في المحرك.';
        message.dataset.state = 'error';
    } else if (status.installed) {
        message.textContent = 'محرك CUDA موجود. فعّل المفتاح لاستخدامه في المهام الجديدة.';
        message.dataset.state = 'success';
    } else {
        message.textContent = 'محرك CPU المضمن نشط. يتطلب CUDA بطاقة NVIDIA وتعريفاً متوافقاً.';
        message.dataset.state = 'ready';
    }
}

async function downloadCudaEngine(): Promise<void> {
    if (activeEngineRuns > 0 || cudaDownloadInProgress) return;
    const status = document.getElementById('cuda-engine-status');
    const progress = document.getElementById('cuda-download-progress');
    const fill = progress?.querySelector<HTMLElement>('.progress-fill');
    const text = progress?.querySelector<HTMLElement>('.progress-text');
    if (!status || !progress || !fill || !text) return;

    cudaDownloadInProgress = true;
    progress.hidden = false;
    fill.style.width = '0%';
    text.textContent = 'جاري الاتصال بخادم الإصدار…';
    status.textContent = 'جارٍ تنزيل الأجزاء والتحقق من بصمة SHA-256. لا تغلق التطبيق.';
    status.dataset.state = 'loading';
    updateCudaControls();

    let unlisten: (() => void) | undefined;
    try {
        unlisten = await listen<CudaDownloadProgress>('cuda-download-progress', (event) => {
            const { downloaded, total } = event.payload;
            const percent = total > 0 ? Math.min(100, Math.floor((downloaded / total) * 100)) : 0;
            fill.style.width = `${percent}%`;
            text.textContent = `${percent}% — ${formatFileSize(downloaded)} / ${formatFileSize(total)}`;
        });
        await invoke<string>('install_cuda_engine');
        localStorage.setItem(cudaEnabledStorageKey, 'true');
        status.textContent = 'اكتمل التنزيل والتحقق. سيستخدم CUDA في المهام التالية.';
        status.dataset.state = 'success';
        await refreshCudaEngineStatus();
    } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        status.textContent = `تعذر تثبيت محرك CUDA: ${detail}`;
        status.dataset.state = 'error';
        console.error('تعذر تثبيت محرك CUDA:', error);
    } finally {
        unlisten?.();
        cudaDownloadInProgress = false;
        updateCudaControls();
    }
}

function updateQueueView(): void {
    const activeCount = queue.filter((job) => job.status === 'queued' || job.status === 'running').length;
    const queueCount = document.getElementById('queue-count');
    if (queueCount) queueCount.textContent = String(activeCount);

    const queueList = document.getElementById('queue-list');
    if (!queueList) return;
    queueList.replaceChildren();
    if (queue.length === 0) {
        const emptyState = document.createElement('p');
        emptyState.className = 'empty-state';
        emptyState.textContent = 'لا توجد مهام في القائمة.';
        queueList.append(emptyState);
        return;
    }

    for (const job of queue) {
        const row = document.createElement('article');
        row.className = `queue-item queue-item-${job.status}`;
        const heading = document.createElement('div');
        heading.className = 'queue-item-heading';
        const title = document.createElement('strong');
        title.textContent = job.label;
        const status = document.createElement('span');
        status.className = 'queue-item-status';
        status.textContent = job.status === 'queued' ? 'في الانتظار'
            : job.status === 'running' ? 'قيد التنفيذ'
                : job.status === 'completed' ? 'اكتملت'
                    : 'فشلت';
        const message = document.createElement('p');
        message.textContent = job.message;
        heading.append(title, status);
        row.append(heading, message);
        queueList.append(row);
    }
}

function updateProgress(toolType: ToolType, output: string): void {
    const container = document.querySelector<HTMLElement>(`[data-progress-for="${toolType}"]`);
    const bar = container?.querySelector<HTMLElement>('.progress-fill');
    const text = container?.querySelector<HTMLElement>('.progress-text');
    if (!bar || !text) return;

    const match = output.match(/تمت معالجة\s+(\d+)\/(\d+)\s+إطار/);
    if (match) {
        const current = Number(match[1]);
        const total = Number(match[2]);
        const percent = total > 0 ? Math.round((current / total) * 100) : 0;
        bar.style.width = `${percent}%`;
        text.textContent = `${percent}% (${current}/${total}) إطار`;
        setStatus(toolType, 'جاري تحسين الإطارات...', 'running');
    } else if (output.includes('التصدير النهائي')) {
        bar.style.width = '90%';
        text.textContent = '90% — جاري التصدير';
        setStatus(toolType, 'جاري التصدير النهائي...', 'running');
    }
}

async function executeQueueJob(job: QueueJob): Promise<void> {
    await runPythonCommand(job.configPath, (line) => {
        if (line.trim()) console.info(`[Mizmar] ${line}`);
        updateProgress(job.toolType, line);
    });
}

async function processQueue(): Promise<void> {
    if (queueRunning) return;
    queueRunning = true;
    try {
        while (true) {
            const job = queue.find((item) => item.status === 'queued');
            if (!job) break;
            job.status = 'running';
            job.message = 'قيد التنفيذ';
            const progress = document.querySelector<HTMLElement>(`[data-progress-for="${job.toolType}"]`);
            if (progress) {
                progress.hidden = false;
                progress.querySelector<HTMLElement>('.progress-fill')!.style.width = '0%';
                progress.querySelector<HTMLElement>('.progress-text')!.textContent = '0%';
            }
            setStatus(job.toolType, `قيد التنفيذ: ${job.label}`, 'running');
            updateQueueView();

            try {
                await executeQueueJob(job);
                job.status = 'completed';
                job.message = 'اكتملت المهمة بنجاح';
                const bar = progress?.querySelector<HTMLElement>('.progress-fill');
                const text = progress?.querySelector<HTMLElement>('.progress-text');
                if (bar) bar.style.width = '100%';
                if (text) text.textContent = 'اكتملت المعالجة';
                setStatus(job.toolType, `اكتملت المهمة: ${job.label}`, 'success');
                setToolReady(job.toolType, Boolean(selectedPaths[job.toolType]));
            } catch (error) {
                job.status = 'failed';
                job.message = error instanceof Error ? error.message : String(error);
                setStatus(job.toolType, `فشلت المهمة: ${job.message}`, 'error');
                setToolReady(job.toolType, Boolean(selectedPaths[job.toolType]));
                console.error(`فشلت المهمة ${job.label}:`, error);
            }
            updateQueueView();
        }
    } finally {
        queueRunning = false;
        updateQueueView();
    }
}

async function startTool(toolType: ToolType, button: HTMLButtonElement): Promise<void> {
    if (!selectedPaths[toolType]) {
        setStatus(toolType, toolInfo[toolType].inputHint, 'error');
        return;
    }

    setToolReady(toolType, false);
    button.disabled = true;
    let queued = false;
    try {
        const config = buildConfig(toolType);
        const jobId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const configPath = await saveJobConfig(jobId, config);
        const fileName = selectedPaths[toolType].split(/[\\/]/).pop() || selectedPaths[toolType];
        queue.push({
            id: jobId,
            label: `${toolInfo[toolType].title}: ${fileName}`,
            toolType,
            configPath,
            status: 'queued',
            message: 'بانتظار التنفيذ'
        });
        queued = true;
        updateQueueView();
        setStatus(toolType, `تم حفظ الإعدادات وإضافة المهمة: ${fileName}`, 'queued');
        void processQueue();
    } catch (error) {
        console.error('تعذر حفظ إعدادات المهمة:', error);
        setStatus(toolType, `تعذر بدء المهمة: ${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
        if (queued) {
            button.disabled = false;
            button.classList.remove('is-ready');
        } else {
            setToolReady(toolType, true);
        }
    }
}

document.querySelectorAll<HTMLElement>('[data-page]').forEach((element) => {
    element.addEventListener('click', (event) => {
        if (element instanceof HTMLAnchorElement) event.preventDefault();
        const page = element.dataset.page;
        if (page === 'dashboard' || page === 'queue' || page === 'help' || page === 'settings' || isToolType(page)) {
            showPage(page);
        }
    });
});

document.querySelectorAll<HTMLElement>('[data-drop-zone]').forEach((zone) => {
    const toolType = zone.dataset.dropZone;
    if (!isToolType(toolType)) return;
    zone.addEventListener('click', () => void chooseVideo(toolType));
    zone.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            void chooseVideo(toolType);
        }
    });
});

document.querySelectorAll<HTMLButtonElement>('[data-start-tool]').forEach((button) => {
    const toolType = button.dataset.startTool;
    if (isToolType(toolType)) {
        button.addEventListener('click', () => void startTool(toolType, button));
    }
});

document.querySelectorAll<HTMLButtonElement>('.res-card').forEach((button) => {
    button.addEventListener('click', () => {
        document.querySelectorAll('.res-card').forEach((card) => card.classList.remove('active'));
        button.classList.add('active');
    });
});

const blurAmount = document.getElementById('blur-amount-select') as HTMLInputElement | null;
blurAmount?.addEventListener('input', () => {
    const value = document.getElementById('blur-amount-value');
    if (value) value.textContent = blurAmount.value;
});

document.getElementById('clear-queue-btn')?.addEventListener('click', () => {
    for (let index = queue.length - 1; index >= 0; index -= 1) {
        if (queue[index].status === 'completed' || queue[index].status === 'failed') {
            queue.splice(index, 1);
        }
    }
    updateQueueView();
});

document.getElementById('cuda-enabled-toggle')?.addEventListener('change', (event) => {
    const toggle = event.currentTarget as HTMLInputElement;
    if (!cudaEngineInstalled) {
        toggle.checked = false;
        return;
    }
    localStorage.setItem(cudaEnabledStorageKey, String(toggle.checked));
    const status = document.getElementById('cuda-engine-status');
    if (status) {
        status.textContent = toggle.checked
            ? 'تم تفعيل CUDA للمهام الجديدة.'
            : 'تم إيقاف CUDA؛ ستستخدم المهام الجديدة محرك CPU.';
        status.dataset.state = 'ready';
    }
});

document.getElementById('cuda-download-button')?.addEventListener('click', () => {
    void downloadCudaEngine();
});

document.querySelectorAll<HTMLButtonElement>('[data-start-tool]').forEach((button) => {
    const toolType = button.dataset.startTool;
    if (isToolType(toolType)) setToolReady(toolType, false);
});

try {
    void getCurrentWindow().onDragDropEvent((event) => {
        const toolType = currentPage;
        if (!isToolType(toolType)) return;
        const zone = document.querySelector<HTMLElement>(`[data-drop-zone="${toolType}"]`);
        if (!zone) return;

        if (event.payload.type === 'enter' || event.payload.type === 'over') {
            zone.classList.add('drag-over');
        } else {
            zone.classList.remove('drag-over');
        }
        if (event.payload.type === 'drop' && event.payload.paths.length > 0) {
            setSelectedVideo(toolType, event.payload.paths[0]);
        }
    }).catch((error: unknown) => {
        console.error('تعذر تفعيل السحب والإفلات:', error);
    });
} catch (error) {
    console.warn('السحب والإفلات متاحان داخل تطبيق Tauri فقط:', error);
}

updateQueueView();
void refreshCudaEngineStatus().catch((error: unknown) => {
    const status = document.getElementById('cuda-engine-status');
    if (status) {
        status.textContent = `تعذر قراءة حالة محرك CUDA: ${error instanceof Error ? error.message : String(error)}`;
        status.dataset.state = 'error';
    }
    console.error('تعذر قراءة حالة محرك CUDA:', error);
});
