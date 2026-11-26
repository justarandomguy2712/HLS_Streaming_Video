param(
    [string] $BaseUrl = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')

$health = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/health" -TimeoutSec 10
$body = $health.Content | ConvertFrom-Json
if ($health.StatusCode -ne 200 -or $body.status -ne 'ok' -or $body.database -ne 'ok') {
    throw 'Health check hoặc SQLite không hoạt động.'
}
Write-Output 'PASS GET /health (200, SQLite ok)'

$schema = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/openapi.json" -TimeoutSec 10
$api = $schema.Content | ConvertFrom-Json
if ($schema.StatusCode -ne 200 -or -not $api.paths.'/health'.get.responses.'503') {
    throw 'OpenAPI thiếu endpoint /health hoặc mô tả phản hồi 503.'
}
Write-Output 'PASS GET /openapi.json (200, /health có mô tả 503)'

$docs = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/docs" -TimeoutSec 10
if ($docs.StatusCode -ne 200 -or $docs.Content -notmatch 'SwaggerUIBundle' -or
    $docs.Content -notmatch '/openapi\.json') {
    throw 'Trang Swagger UI không tải cấu hình OpenAPI.'
}
Write-Output 'PASS GET /docs (200, Swagger UI)'

$index = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/" -TimeoutSec 10
if ($index.StatusCode -ne 200 -or $index.Content -notmatch 'href="/docs"') {
    throw 'Trang HTML không tải hoặc thiếu liên kết Swagger.'
}
Write-Output 'PASS GET / (200, HTML)'
