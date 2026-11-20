param(
    [switch]$SkipTauriBuild,
    [switch]$CpuEngineOnly,
    [string]$CudaReleaseTag = 'v0.1.0'
)

$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$uiRoot = Join-Path $projectRoot 'mizmar-ui'
$requirementsFile = Join-Path $projectRoot 'requirements.txt'
$cpuEnvironment = Join-Path $projectRoot 'venv-cpu'
$cudaEnvironment = Join-Path $projectRoot 'venv'
$existingCudaPython = Join-Path $cudaEnvironment 'Scripts\python.exe'
$hasCudaEnvironment = $false
if (Test-Path -LiteralPath $existingCudaPython -PathType Leaf) {
    try {
        & $existingCudaPython -c 'import torch, sys; sys.exit(0 if torch.version.cuda is not None else 1)' 2>$null
        $hasCudaEnvironment = $LASTEXITCODE -eq 0
    }
    catch {
        $hasCudaEnvironment = $false
    }
}
if (-not $hasCudaEnvironment) {
    $cudaEnvironment = Join-Path $projectRoot 'venv-cuda'
}
$cpuPython = Join-Path $cpuEnvironment 'Scripts\python.exe'
$cudaPython = Join-Path $cudaEnvironment 'Scripts\python.exe'
$ffmpegDirectory = Join-Path $uiRoot 'src-tauri\resources\ffmpeg'
$sidecarDirectory = Join-Path $uiRoot 'src-tauri\binaries'
$buildDirectory = Join-Path $projectRoot 'build\pyinstaller'
$releaseDirectory = Join-Path $projectRoot "release-assets\cuda\$CudaReleaseTag"

function Invoke-CheckedCommand {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Description
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Apply-BasicSrCompatibilityPatch {
    param([string]$PythonPath)

    $environmentDirectory = Split-Path (Split-Path $PythonPath -Parent) -Parent
    $degradationsFile = Join-Path $environmentDirectory 'Lib\site-packages\basicsr\data\degradations.py'
    if (-not (Test-Path -LiteralPath $degradationsFile -PathType Leaf)) {
        throw "BasicSR compatibility file was not found: $degradationsFile"
    }

    $source = [System.IO.File]::ReadAllText($degradationsFile)
    $legacyImport = 'from torchvision.transforms.functional_tensor import rgb_to_grayscale'
    $supportedImport = 'from torchvision.transforms.functional import rgb_to_grayscale'
    if ($source.Contains($legacyImport)) {
        $source = $source.Replace($legacyImport, $supportedImport)
        [System.IO.File]::WriteAllText(
            $degradationsFile,
            $source,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    elseif (-not $source.Contains($supportedImport)) {
        throw 'BasicSR import changed unexpectedly; review its torchvision compatibility before packaging.'
    }
}

function Initialize-EngineEnvironment {
    param(
        [string]$EnvironmentPath,
        [string]$PythonPath,
        [ValidateSet('cpu', 'cuda')]
        [string]$Flavor
    )

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
            throw 'Python Launcher (py) is required to create the Python 3.11 build environments.'
        }
        Invoke-CheckedCommand 'py' @('-3.11', '-m', 'venv', $EnvironmentPath) "Creating the $Flavor Python environment"
    }

    $torchCheck = if ($Flavor -eq 'cpu') {
        'import torch, sys; sys.exit(0 if torch.version.cuda is None else 1)'
    }
    else {
        'import torch, sys; sys.exit(0 if torch.version.cuda is not None else 1)'
    }
    $torchCheckExitCode = 1
    try {
        & $PythonPath -c $torchCheck 2>$null
        $torchCheckExitCode = $LASTEXITCODE
    }
    catch {
        $torchCheckExitCode = $LASTEXITCODE
    }
    if ($torchCheckExitCode -ne 0) {
        if ($Flavor -eq 'cpu') {
            Invoke-CheckedCommand $PythonPath @(
                '-m', 'pip', 'install',
                '--no-cache-dir',
                '--retries', '3',
                '--timeout', '60',
                '--index-url', 'https://download.pytorch.org/whl/cpu',
                '--extra-index-url', 'https://pypi.org/simple',
                'torch==2.5.1+cpu',
                'torchvision==0.20.1+cpu'
            ) 'Installing CPU-only PyTorch'
        }
        else {
            Invoke-CheckedCommand $PythonPath @(
                '-m', 'pip', 'install',
                '--no-cache-dir',
                '--retries', '3',
                '--timeout', '60',
                '--index-url', 'https://download.pytorch.org/whl/cu121',
                '--extra-index-url', 'https://pypi.org/simple',
                'torch==2.5.1+cu121',
                'torchvision==0.20.1+cu121'
            ) 'Installing CUDA PyTorch'
        }
    }

    Invoke-CheckedCommand $PythonPath @('-m', 'pip', 'install', '-r', $requirementsFile) "Installing $Flavor engine requirements"
    $torchCheckExitCode = 1
    try {
        & $PythonPath -c $torchCheck 2>$null
        $torchCheckExitCode = $LASTEXITCODE
    }
    catch {
        $torchCheckExitCode = $LASTEXITCODE
    }
    if ($torchCheckExitCode -ne 0) {
        throw "The requirements changed the installed PyTorch flavor; expected a $Flavor build environment."
    }
    Apply-BasicSrCompatibilityPatch $PythonPath
    Invoke-CheckedCommand $PythonPath @('-m', 'pip', 'install', 'pyinstaller==6.22.3') "Installing PyInstaller for $Flavor"
}

function Build-PyInstallerEngine {
    param(
        [string]$PythonPath,
        [string]$EngineName,
        [string]$Flavor
    )

    $flavorBuildDirectory = Join-Path $buildDirectory $Flavor
    $arguments = @(
        '--noconfirm',
        '--clean',
        '--onefile',
        '--console',
        '--name', $EngineName,
        '--distpath', (Join-Path $flavorBuildDirectory 'dist'),
        '--workpath', (Join-Path $flavorBuildDirectory 'work'),
        '--specpath', $flavorBuildDirectory,
        '--add-data', "$(Join-Path $projectRoot 'models');models",
        '--collect-all', 'torch',
        '--collect-all', 'torchvision',
        '--collect-all', 'cv2',
        '--collect-all', 'basicsr',
        '--collect-all', 'realesrgan',
        '--collect-all', 'gfpgan',
        '--collect-all', 'tiktok_quality',
        '--collect-all', 'yt_dlp',
        '--hidden-import', 'core.upscale',
        '--hidden-import', 'core.pipeline',
        '--hidden-import', 'core.interpolate',
        '--hidden-import', 'core.export',
        (Join-Path $projectRoot 'run_upscale.py')
    )

    Write-Host "Building $Flavor engine..."
    Invoke-CheckedCommand $PythonPath (@('-m', 'PyInstaller') + $arguments) "Building the $Flavor engine"
    $outputPath = Join-Path $flavorBuildDirectory "dist\$EngineName.exe"
    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
        throw "PyInstaller output was not found: $outputPath"
    }
    Write-Host "Built engine: $outputPath"
}

if ($CudaReleaseTag -notmatch '^[A-Za-z0-9._-]+$') {
    throw 'CudaReleaseTag may contain only letters, digits, dot, underscore, and hyphen.'
}

if (-not $SkipTauriBuild) {
    foreach ($binary in @('ffmpeg.exe', 'ffprobe.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $ffmpegDirectory $binary) -PathType Leaf)) {
            throw "Missing $binary in '$ffmpegDirectory'. Add a legally distributable FFmpeg build, then retry."
        }
    }
}

Initialize-EngineEnvironment $cpuEnvironment $cpuPython 'cpu'
if (-not $CpuEngineOnly) {
    Initialize-EngineEnvironment $cudaEnvironment $cudaPython 'cuda'
}

Build-PyInstallerEngine $cpuPython 'mizmar-engine-cpu' 'cpu'
$cpuEngine = Join-Path $buildDirectory 'cpu\dist\mizmar-engine-cpu.exe'
if (-not $CpuEngineOnly) {
    Build-PyInstallerEngine $cudaPython 'mizmar-engine-cuda' 'cuda'
    $cudaEngine = Join-Path $buildDirectory 'cuda\dist\mizmar-engine-cuda.exe'
}

$rustInfo = & rustc -vV
if ($LASTEXITCODE -ne 0) {
    throw 'rustc is not available. Install the Rust toolchain required by Tauri.'
}
$hostLine = $rustInfo | Select-String '^host:\s+(.+)$'
if (-not $hostLine) {
    throw 'Could not determine the Rust target triple from rustc -vV.'
}
$targetTriple = $hostLine.Matches[0].Groups[1].Value
$cpuSidecar = Join-Path $sidecarDirectory "mizmar-engine-cpu-$targetTriple.exe"
$cpuSize = (Get-Item -LiteralPath $cpuEngine).Length
if ($cpuSize -ge 1GB) {
    throw "CPU engine is $([math]::Round($cpuSize / 1GB, 2)) GiB; expected it to remain below 1 GiB."
}
New-Item -ItemType Directory -Force -Path $sidecarDirectory | Out-Null
Copy-Item -LiteralPath $cpuEngine -Destination $cpuSidecar -Force

$cudaHash = $null
if (-not $CpuEngineOnly) {
    $cudaFileName = "mizmar-engine-cuda-$targetTriple.exe"
    New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
    $cudaHash = (Get-FileHash -LiteralPath $cudaEngine -Algorithm SHA256).Hash.ToLowerInvariant()
    $cudaSize = (Get-Item -LiteralPath $cudaEngine).Length

    $partSize = 1536L * 1024L * 1024L
    $buffer = New-Object byte[] (8MB)
    $partMetadata = [System.Collections.Generic.List[object]]::new()
    $inputStream = [System.IO.File]::OpenRead($cudaEngine)
    try {
        $partNumber = 1
        while ($inputStream.Position -lt $inputStream.Length) {
            $partName = '{0}.part{1:D3}' -f $cudaFileName, $partNumber
            $partPath = Join-Path $releaseDirectory $partName
            $partStream = [System.IO.File]::Create($partPath)
            $partBytes = [long]0
            try {
                while ($partBytes -lt $partSize -and $inputStream.Position -lt $inputStream.Length) {
                    $remaining = [Math]::Min([long]$buffer.Length, $partSize - $partBytes)
                    $read = $inputStream.Read($buffer, 0, [int]$remaining)
                    if ($read -le 0) {
                        throw 'Unexpected end of CUDA engine while splitting release assets.'
                    }
                    $partStream.Write($buffer, 0, $read)
                    $partBytes += $read
                }
            }
            finally {
                $partStream.Dispose()
            }
            if ($partBytes -ge 2GB) {
                throw "GitHub release asset '$partName' is too large; each asset must be under 2 GiB."
            }
            $partMetadata.Add([ordered]@{ name = $partName; size = $partBytes })
            $partNumber += 1
        }
    }
    finally {
        $inputStream.Dispose()
    }

    $manifest = [ordered]@{
        filename = $cudaFileName
        size = $cudaSize
        sha256 = $cudaHash
        parts = @($partMetadata)
    }
    $manifestPath = Join-Path $releaseDirectory "mizmar-engine-cuda-$targetTriple.manifest.json"
    $manifestJson = $manifest | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText(
        $manifestPath,
        $manifestJson,
        [System.Text.UTF8Encoding]::new($false)
    )
}

Write-Host "CPU Sidecar (<1 GiB): $cpuSidecar"
if (-not $CpuEngineOnly) {
    Write-Host "CUDA engine: $cudaEngine ($([math]::Round($cudaSize / 1GB, 2)) GiB)"
    Write-Host "CUDA SHA-256: $cudaHash"
    Write-Host "Upload the manifest and every part listed in it from '$releaseDirectory' to '$CudaReleaseTag'."
    Write-Host 'Set the manifest URL and this SHA-256 in src-tauri/src/engine.rs, then rebuild the CPU installer.'
}
else {
    Write-Host 'CUDA build and release packaging skipped.'
}

if ($SkipTauriBuild) {
    Write-Host 'Tauri build skipped.'
    exit 0
}

Push-Location $uiRoot
try {
    npm run tauri -- build
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

Write-Host 'Installer bundles are under mizmar-ui\src-tauri\target\release\bundle.'
