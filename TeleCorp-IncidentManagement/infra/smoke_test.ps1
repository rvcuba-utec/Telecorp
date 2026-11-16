<#
  Prueba de humo contra el despliegue real. Uso: .\infra\smoke_test.ps1
  Cubre: health y catálogo, seguridad, intake idempotente, consulta con verificación, eventos de AWS con el
  contrato real (sobre común + X-API-Key), alerta PREVENTIVA, y 10 usuarios -> incidencia -> orquestación.
#>
$ErrorActionPreference = 'Stop'
$o = Get-Content (Join-Path $PSScriptRoot 'outputs.local.json') -Raw | ConvertFrom-Json
$api = $o.apiBase; $tok = $o.operatorToken; $key = $o.azureInboundApiKey
$run = [int](Get-Date -UFormat %s)
$fail = 0
$zone = 'LIM-VES'; $ant = 'ANT-LIM-056'      # zona residencial poco usada en la demo
function Check($name, $cond, $info = '') { if ($cond) { Write-Host "  OK   $name" -ForegroundColor Green } else { Write-Host "  FAIL $name $info" -ForegroundColor Red; $script:fail++ } }
function Call($method, $path, $body = $null, $headers = @{}) {
  try {
    $p = @{ Uri = "$api/api$path"; Method = $method; Headers = $headers; UseBasicParsing = $true; ContentType = 'application/json; charset=utf-8'; TimeoutSec = 60 }
    if ($body) { $p.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 6)) }
    $r = Invoke-WebRequest @p
    return @{ code = [int]$r.StatusCode; json = ($r.Content | ConvertFrom-Json) }
  } catch {
    $c = 0
    if ($_.Exception.Response) { $c = [int]$_.Exception.Response.StatusCode }
    return @{ code = $c; json = $null }
  }
}
function Now { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
function Report($i, $z) {
  @{ idempotency_key = "smoke-$run-$i-key"; user_document = ("S{0}{1:D4}" -f ($run % 100000), $i); phone = ("+5197{0:D7}" -f $i)
     email = "smoke$i@example.com"; zone_id = $z; occurred_at = (Now); category = 'SIN_INTERNET'; description = "prueba de humo $i" }
}
function AwsEvent($type, $state, $antenna, $extra = @{}) {
  $p = @{ antenna_id = $antenna; zone_id = $zone; episode_id = "EP-$antenna-$run"; event_type = $type; severity = 'ALTA'
          previous_state = 'NORMAL'; new_state = $state; gb_per_min = 40.0; first_anomaly_at = (Now); detected_at = (Now) }
  foreach ($k in $extra.Keys) { $p[$k] = $extra[$k] }
  @{ event_id = [guid]::NewGuid().ToString(); type = 'antenna.event'; schema_version = '1.0'; source = 'aws'
     occurred_at = (Now); correlation_id = "smoke-$run"; payload = $p }
}
$OP = @{ 'x-operator-token' = $tok }
$AWS = @{ 'X-API-Key' = $key }

Write-Host "`n[1] Salud, catálogo y seguridad"
Check 'GET /health = 200' ((Call GET '/health').code -eq 200)
$c = Call GET '/catalog'; Check 'GET /catalog trae categorías del contrato' ($c.code -eq 200 -and $c.json.category -contains 'SIN_INTERNET')
Check 'GET /incidents sin token = 401' ((Call GET '/incidents').code -eq 401)
Check 'GET /incidents con token = 200' ((Call GET '/incidents' $null $OP).code -eq 200)
Check 'POST /events/infra sin X-API-Key = 401' ((Call POST '/events/infra' (AwsEvent 'FALLA' 'CAIDA' $ant)).code -eq 401)
$s = Call GET '/settings' $null $OP; Check 'GET /settings = 200' ($s.code -eq 200)
if ($s.json) { Write-Host "     config=$($s.json.source) AWS=$($s.json.integrations.aws) GCP=$($s.json.integrations.gcp) email=$($s.json.integrations.email)" }

Write-Host "`n[2] Intake idempotente y consulta con verificación"
$rep = Report 0 'LIM-SURCO'
$a = Call POST '/reports' $rep; $b = Call POST '/reports' $rep
Check 'primer POST = 202' ($a.code -eq 202)
Check 'repetido = 200 y mismo id' ($b.code -eq 200 -and $b.json.report_id -eq $a.json.report_id)
$rid = $a.json.report_id
Check 'consulta sin verificación = 404' ((Call GET "/reports/$rid").code -eq 404)
$g = Call GET "/reports/$rid`?verify=$($rep.phone.Substring($rep.phone.Length - 4))"
Check 'consulta con verificación = 200 sin PII' ($g.code -eq 200 -and ($g.json | ConvertTo-Json -Depth 6) -notmatch 'smoke0@|S\d{5}0000')
Check 'esquema inválido = 400' ((Call POST '/reports' @{ zone_id = 'Z99' }).code -eq 400)

Write-Host "`n[3] Eventos de AWS (contrato real, idempotencia)"
$ev = AwsEvent 'FALLA' 'CAIDA' $ant
Check 'evento nuevo = 202' ((Call POST '/events/infra' $ev $AWS).code -eq 202)
$d = Call POST '/events/infra' $ev $AWS
Check 'evento repetido = 200 duplicate' ($d.code -eq 200 -and $d.json.status -eq 'duplicate')
$bad = AwsEvent 'ANTENNA_DOWN' 'CAIDA' $ant
Check 'tipo fuera del contrato = 400' ((Call POST '/events/infra' $bad $AWS).code -eq 400)
$pred = @{ probability = 0.85; predicted_state = 'CAIDA'; horizon_minutes = 60; valid_until = (Get-Date).ToUniversalTime().AddHours(2).ToString('yyyy-MM-ddTHH:mm:ssZ'); model_version = 'smoke' }
Check 'PREVENTIVA = 202' ((Call POST '/events/infra' (AwsEvent 'PREVENTIVA' 'NORMAL' 'ANT-LIM-057' @{ prediction = $pred }) $AWS).code -eq 202)

Write-Host "`n[4] 10 usuarios distintos en $zone -> incidencia colectiva (espera hasta 90 s)"
1..10 | ForEach-Object { Call POST '/reports' (Report (100 + $_) $zone) | Out-Null }
$inc = $null
foreach ($i in 1..18) {
  Start-Sleep 5
  $l = Call GET "/incidents?zone_id=$zone&incident_type=COLECTIVA" $null $OP
  if ($l.code -eq 200 -and $l.json.items.Count -ge 1) { $inc = $l.json.items[0]; break }
}
Check 'incidencia creada por el correlador' ($null -ne $inc)
if ($inc) {
  Write-Host "     $($inc.incident_id) usuarios=$($inc.users_affected) prioridad=$($inc.priority_score) ($($inc.priority_level)) detectada_por=$($inc.detected_by)"
  Check 'usuarios_afectados >= 10' ($inc.users_affected -ge 10)
  Check 'evidencia de antena enlazada' ($inc.antenna_ids -contains $ant)
  Start-Sleep 10
  $d = Call GET "/incidents/$($inc.incident_id)" $null $OP
  Check 'IA/reglas aplicada (orquestación corrió)' ($d.code -eq 200 -and $d.json.ai_source)
  Check 'historial presente' ($d.json.history.Count -ge 3)
  Write-Host "     estado=$($d.json.status)  ia=$($d.json.ai_source)"
}
$p = Call GET "/incidents?zone_id=$zone&incident_type=PREVENTIVA" $null $OP
Check 'incidencia PREVENTIVA creada' ($p.code -eq 200 -and $p.json.items.Count -ge 1)
Check 'GET /antennas = 200' ((Call GET "/antennas?zone_id=$zone" $null $OP).code -eq 200)
Check 'GET /predictions = 200' ((Call GET "/predictions?zone_id=$zone" $null $OP).code -eq 200)
Write-Host ""
if ($fail -eq 0) { Write-Host "TODO OK" -ForegroundColor Green } else { Write-Host "$fail comprobación(es) fallaron" -ForegroundColor Red; exit 1 }
