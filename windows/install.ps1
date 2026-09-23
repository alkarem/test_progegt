# تثبيت وتشغيل «نظام مراقبة الاعتمادات والمصروفات الحكومية» على Windows.
# المتطلب الوحيد: Docker Desktop مثبت ويعمل. يُشغَّل من install.cmd (نقرتان).
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $PSScriptRoot)

function Say($msg, $color = "Cyan") { Write-Host "`n>> $msg" -ForegroundColor $color }
function Fail($msg) { Write-Host "`n!! $msg" -ForegroundColor Red; Read-Host "اضغط Enter للإغلاق"; exit 1 }
function RandomHex([int]$bytes) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return -join ($b | ForEach-Object { $_.ToString("x2") })
}
function RandomB64([int]$bytes) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return [Convert]::ToBase64String($b)
}

# 1) Docker
Say "التحقق من Docker Desktop..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker غير مثبت. ثبّت Docker Desktop من https://www.docker.com/products/docker-desktop/ ثم أعد التشغيل."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop لا يعمل. افتحه وانتظر حتى يظهر (Engine running) ثم أعد تشغيل هذا الملف." }

# 2) الإعدادات والأسرار (مرة واحدة)
if (-not (Test-Path ".env")) {
    Say "إنشاء ملف الإعدادات .env بأسرار عشوائية..."
    $envText = Get-Content "deploy\env.example" -Raw -Encoding UTF8
    $envText = $envText -replace "(?m)^POSTGRES_PASSWORD=.*$", ("POSTGRES_PASSWORD=" + (RandomHex 24))
    $envText = $envText -replace "(?m)^GBCFMS_JWT_SECRET=.*$", ("GBCFMS_JWT_SECRET=" + (RandomHex 48))
    $envText = $envText -replace "(?m)^GBCFMS_BACKUP_KEY=.*$", ("GBCFMS_BACKUP_KEY=" + (RandomB64 32))
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), $envText, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "مهم: احفظ نسخة من ملف .env في مكان آمن خارج هذا الجهاز؛ مفتاح GBCFMS_BACKUP_KEY لازم لاستعادة النسخ الاحتياطية." -ForegroundColor Yellow
} else {
    Say "ملف .env موجود؛ تُستخدم الإعدادات الحالية."
}
New-Item -ItemType Directory -Force -Path "backups" | Out-Null

# 3) البناء والتشغيل
Say "بناء النظام (المرة الأولى قد تستغرق 5–15 دقيقة حسب الإنترنت)..."
docker compose build
if ($LASTEXITCODE -ne 0) { Fail "فشل البناء. تأكد من اتصال الإنترنت ثم أعد المحاولة." }

Say "تشغيل الخدمات..."
docker compose up -d
if ($LASTEXITCODE -ne 0) { Fail "فشل التشغيل. راجع: docker compose logs" }

Say "انتظار جاهزية الخادم..."
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    $id = docker compose ps -q api
    if ($id) {
        $state = docker inspect --format "{{.State.Health.Status}}" $id 2>$null
        if ($state -eq "healthy") { $ready = $true; break }
    }
    Start-Sleep -Seconds 5
}
if (-not $ready) { Fail "الخادم لم يصبح جاهزًا خلال 5 دقائق. راجع السجلات: docker compose logs api" }

# 4) المسؤول الأول والبيانات المرجعية (لا يعيد إنشاء المسؤول إن وُجد)
Say "إعداد الصلاحيات والمسؤول الأول (admin)..."
Write-Host "إن طُلبت كلمة مرور: 12 حرفًا على الأقل، وتجمع حروفًا وأرقامًا، ولا تحتوي كلمة admin." -ForegroundColor Yellow
$ok = $false
for ($i = 0; $i -lt 3 -and -not $ok; $i++) {
    docker compose run --rm api bootstrap
    $ok = ($LASTEXITCODE -eq 0)
    if (-not $ok) { Write-Host "كلمة المرور لا تستوفي السياسة، حاول مجددًا." -ForegroundColor Yellow }
}
if (-not $ok) { Fail "تعذر إنشاء المسؤول." }
docker compose run --rm api seed-reference
if ($LASTEXITCODE -ne 0) { Fail "تعذر تحميل البيانات المرجعية." }

$port = (Select-String -Path ".env" -Pattern "^HTTPS_PORT=(\d+)" | ForEach-Object { $_.Matches[0].Groups[1].Value })
$url = if ($port -and $port -ne "443") { "https://localhost:$port" } else { "https://localhost" }
Say "اكتمل التثبيت. افتح: $url" "Green"
Write-Host "اسم المستخدم: admin  —  سيطلب النظام تغيير كلمة المرور عند أول دخول."
Write-Host "المتصفح سيحذّر من الشهادة (موقعة ذاتيًا): اختر «متقدم» ثم «المتابعة إلى localhost»."
Write-Host "من أجهزة أخرى في الشبكة: https://<اسم-هذا-الجهاز أو عنوان IP>"
Start-Process $url
Read-Host "اضغط Enter للإغلاق"
