<#
  PulsoRed - despliegue completo en Azure (PowerShell 5.1+).  Uso:   .\infra\deploy.ps1
  Idempotente: se puede volver a ejecutar. Región por defecto: westus2 (la suscripción solo permite
  westus2, canadacentral, brazilsouth, chilecentral, westus).
  Salidas (con secretos, NO versionar): infra\outputs.local.json
  Variables globales (URLs y claves de AWS/GCP, reglas): archivo .env en la raíz (plantilla: .env.example).
  Cuando AWS/GCP estén listos: edita .env y ejecuta   .\infra\deploy.ps1 -SettingsOnly
#>
param(
  [string]$Region = 'westus2',
  [string]$RG = 'rg-pulsored',
  [string]$Suffix = '',
  [switch]$NoKeyVaultRefs,      # si las referencias a Key Vault fallan: guarda los secretos directo en app settings
  [switch]$SkipFrontend,
  [switch]$SkipBackend,
  [switch]$SettingsOnly         # solo vuelve a subir las variables del .env (URLs/claves de AWS y GCP, reglas)
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$state = Join-Path $PSScriptRoot 'state.json'
$outFile = Join-Path $PSScriptRoot 'outputs.local.json'

function Step($t) { Write-Host "`n=== $t" -ForegroundColor Cyan }
function Az { # ejecuta az y falla si el código de salida != 0 (los avisos por stderr no cuentan)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  $o = & az.cmd @args 2>&1 | ForEach-Object { "$_" }
  $code = $LASTEXITCODE; $ErrorActionPreference = $prev
  if ($code -ne 0) { throw "az $($args -join ' ') falló:`n$($o -join "`n")" }
  return ($o | Where-Object { $_ -notmatch '^(WARNING|Warning)' }) -join "`n"
}
function AzTry { # true si el comando terminó bien (sirve para "¿existe?" y "intentar sin abortar")
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  & az.cmd @args 2>&1 | Out-Null
  $code = $LASTEXITCODE; $ErrorActionPreference = $prev
  return ($code -eq 0)
}

# ---------- variables globales (.env) ----------
function Read-DotEnv($path) {
  $h = [ordered]@{}
  if (Test-Path $path) {
    foreach ($line in Get-Content $path -Encoding utf8) {
      $t = $line.Trim()
      if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
      $k, $v = $t.Split('=', 2); $h[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
    }
  } else { Write-Host "No existe .env: se usan valores por defecto (copia .env.example a .env)" -ForegroundColor Yellow }
  return $h
}
$dotenv = Read-DotEnv (Join-Path $root '.env')
$secretVars = @{ AWS_API_KEY = 'aws-api-key'; GCP_API_KEY = 'gcp-api-key'; AZURE_INBOUND_API_KEY = 'azure-inbound-api-key' }
$azureManaged = @('COSMOS','SB','OPERATOR_TOKEN','ACS_CONNECTION_STRING','ACS_SENDER','COSMOS_DB')

function Setting($secretName, $value) {
  if ($NoKeyVaultRefs) { return $value }
  return "@Microsoft.KeyVault(SecretUri=https://$($n.kv).vault.azure.net/secrets/$secretName/)"
}
function Set-AppSettings($list) {
  # Por archivo JSON: los paréntesis de @Microsoft.KeyVault(...) rompen az.cmd si van en la línea de comandos.
  $items = foreach ($kv in $list) { if (-not "$kv".Contains('=')) { continue }; $k, $v = "$kv".Split('=', 2); if (-not $k.Trim()) { continue }; [ordered]@{ name = $k.Trim(); value = $v; slotSetting = $false } }
  $file = Join-Path $env:TEMP "pulsored-settings-$Suffix.json"
  [IO.File]::WriteAllText($file, (ConvertTo-Json @($items) -Depth 3), (New-Object Text.UTF8Encoding($false)))
  try { Az functionapp config appsettings set -n $n.func -g $RG --settings "@$file" -o none } finally { Remove-Item $file -ErrorAction SilentlyContinue }
}
function External-Settings {
  # Variables del .env (URLs de AWS/GCP y reglas). Las claves van a Key Vault y la app las lee por referencia.
  if (-not $dotenv['AZURE_INBOUND_API_KEY']) { $dotenv['AZURE_INBOUND_API_KEY'] = $out.azureInboundApiKey }
  $list = @()
  foreach ($k in $dotenv.Keys) {
    if ($azureManaged -contains $k) { continue }
    $v = $dotenv[$k]
    if ($secretVars.ContainsKey($k)) {
      if (-not $v) { continue }
      Az keyvault secret set --vault-name $n.kv -n $secretVars[$k] --value $v -o none | Out-Null
      $list += "$k=$(Setting $secretVars[$k] $v)"
    } elseif ($v -ne '') { $list += "$k=$v" }
    else { $list += "$k=" }   # vacío = modo simulado
  }
  return $list
}

# ---------- sufijo único (se conserva entre ejecuciones) ----------
if (-not $Suffix) {
  if (Test-Path $state) { $Suffix = (Get-Content $state -Raw | ConvertFrom-Json).suffix }
  else { $Suffix = ('pr' + (Get-Random -Minimum 1000 -Maximum 9999)) }
}
@{ suffix = $Suffix; region = $Region; rg = $RG } | ConvertTo-Json | Set-Content $state
$n = @{
  cosmos = "pulsored-cosmos-$Suffix"; sb = "pulsored-sb-$Suffix"; kv = "pulsored-kv-$Suffix"
  st = ("pulsoredst$Suffix").ToLower(); law = "pulsored-law-$Suffix"; ai = "pulsored-ai-$Suffix"
  func = "pulsored-func-$Suffix"; swa = "pulsored-web-$Suffix"; acs = "pulsored-acs-$Suffix"; email = "pulsored-email-$Suffix"
}
$out = @{}
if (Test-Path $outFile) { (Get-Content $outFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $out[$_.Name] = $_.Value } }

Step "0. Sesión y proveedores"
$acct = Az account show -o json | ConvertFrom-Json
Write-Host "Suscripción: $($acct.name)  Región: $Region  Sufijo: $Suffix"
Az config set extension.use_dynamic_install=yes_without_prompt extension.dynamic_install_allow_preview=true | Out-Null
foreach ($p in 'Microsoft.DocumentDB','Microsoft.Web','Microsoft.ServiceBus','Microsoft.KeyVault','Microsoft.Communication','Microsoft.Insights','Microsoft.OperationalInsights','Microsoft.Storage') { Az provider register --namespace $p | Out-Null }

if ($SettingsOnly) {
  Step "Solo variables del .env -> app settings"
  if (-not $out.azureInboundApiKey) { throw 'No hay despliegue previo (infra\outputs.local.json). Ejecuta primero .\infra\deploy.ps1' }
  $ext = External-Settings
  Set-AppSettings $ext
  Write-Host "Listo: $($ext.Count) variables actualizadas en $($n.func)." -ForegroundColor Green
  exit 0
}

Step "1. Grupo de recursos"
Az group create -n $RG -l $Region -o none

Step "2. Cosmos DB (free tier; si no, serverless)"
if (-not (AzTry cosmosdb show -n $n.cosmos -g $RG -o none)) {
  # Respaldo continuo de 7 días (gratis) para restaurar reportes e incidencias (RF-AZ-28); si no se permite, periódico.
  $created = AzTry cosmosdb create -n $n.cosmos -g $RG --locations "regionName=$Region" failoverPriority=0 isZoneRedundant=False `
    --default-consistency-level Session --enable-free-tier true --backup-policy-type Continuous --continuous-tier Continuous7Days -o none
  if (-not $created) {
    $created = AzTry cosmosdb create -n $n.cosmos -g $RG --locations "regionName=$Region" failoverPriority=0 isZoneRedundant=False `
      --default-consistency-level Session --enable-free-tier true -o none
  }
  if (-not $created -and -not (AzTry cosmosdb show -n $n.cosmos -g $RG -o none)) {
    Write-Host "Free tier no disponible -> serverless" -ForegroundColor Yellow
    if (-not (AzTry cosmosdb create -n $n.cosmos -g $RG --locations "regionName=$Region" failoverPriority=0 isZoneRedundant=False `
      --default-consistency-level Session --capabilities EnableServerless --backup-policy-type Continuous --continuous-tier Continuous7Days -o none)) {
      Az cosmosdb create -n $n.cosmos -g $RG --locations "regionName=$Region" failoverPriority=0 isZoneRedundant=False `
        --default-consistency-level Session --capabilities EnableServerless -o none
    }
    $out.cosmosMode = 'serverless'
  } else { $out.cosmosMode = 'free-tier' }
}
$serverless = (Az cosmosdb show -n $n.cosmos -g $RG --query "capabilities[?name=='EnableServerless'] | length(@)" -o tsv) -ne '0'
if (-not (AzTry cosmosdb sql database show -a $n.cosmos -g $RG -n pulsored -o none)) {
  if ($serverless) { Az cosmosdb sql database create -a $n.cosmos -g $RG -n pulsored -o none }
  else { Az cosmosdb sql database create -a $n.cosmos -g $RG -n pulsored --throughput 1000 -o none }   # 1000 RU/s compartidos (gratis)
}
$containers = @(
  @('reports','/zone_id'), @('incidents','/zone_id'), @('incident_users','/incident_id'), @('history','/entity_id'),
  @('infra_events','/zone_id'), @('notifications','/incident_id'), @('zones','/id'), @('leases','/id'))
foreach ($c in $containers) {
  if (-not (AzTry cosmosdb sql container show -a $n.cosmos -g $RG -d pulsored -n $c[0] -o none)) {
    $extra = @(); if ($c[0] -eq 'infra_events') { $extra = @('--ttl', '-1') }   # TTL por documento (30 días)
    Az cosmosdb sql container create -a $n.cosmos -g $RG -d pulsored -n $c[0] -p $c[1] @extra -o none
  }
}
$cosmosConn = (Az cosmosdb keys list -n $n.cosmos -g $RG --type connection-strings --query "connectionStrings[0].connectionString" -o tsv).Trim()

Step "3. Service Bus (Basic) y colas con dead-letter"
if (-not (AzTry servicebus namespace show -n $n.sb -g $RG -o none)) { Az servicebus namespace create -n $n.sb -g $RG -l $Region --sku Basic -o none }
foreach ($q in 'notifications','analytics-export') {
  if (-not (AzTry servicebus queue show --namespace-name $n.sb -g $RG -n $q -o none)) {
    Az servicebus queue create --namespace-name $n.sb -g $RG -n $q --max-delivery-count 10 --lock-duration PT2M -o none
  }
}
$sbConn = (Az servicebus namespace authorization-rule keys list --namespace-name $n.sb -g $RG -n RootManageSharedAccessKey --query primaryConnectionString -o tsv).Trim()

Step "4. Storage, Log Analytics y Application Insights"
if (-not (AzTry storage account show -n $n.st -g $RG -o none)) { Az storage account create -n $n.st -g $RG -l $Region --sku Standard_LRS --allow-blob-public-access false -o none }
if (-not (AzTry monitor log-analytics workspace show -g $RG -n $n.law -o none)) { Az monitor log-analytics workspace create -g $RG -n $n.law -l $Region -o none }
$lawId = (Az monitor log-analytics workspace show -g $RG -n $n.law --query id -o tsv).Trim()
if (-not (AzTry monitor app-insights component show -a $n.ai -g $RG -o none)) {
  Az monitor app-insights component create -a $n.ai -g $RG -l $Region --workspace $lawId --kind web -o none
}
$aiConn = (Az monitor app-insights component show -a $n.ai -g $RG --query connectionString -o tsv).Trim()

Step "5. Function App (Python 3.12, Consumption, identidad administrada)"
if (-not (AzTry functionapp show -n $n.func -g $RG -o none)) {
  Az functionapp create -n $n.func -g $RG --storage-account $n.st --consumption-plan-location $Region `
    --runtime python --runtime-version 3.12 --functions-version 4 --os-type Linux `
    --app-insights $n.ai --assign-identity '[system]' -o none
}
$principal = (Az functionapp identity show -n $n.func -g $RG --query principalId -o tsv).Trim()
$funcHost = (Az functionapp show -n $n.func -g $RG --query defaultHostName -o tsv).Trim()

Step "6. Key Vault y secretos"
if (-not (AzTry keyvault show -n $n.kv -g $RG -o none)) { Az keyvault create -n $n.kv -g $RG -l $Region --enable-rbac-authorization true -o none }
$kvId = (Az keyvault show -n $n.kv -g $RG --query id -o tsv).Trim()
$me = (Az ad signed-in-user show --query id -o tsv).Trim()
[void](AzTry role assignment create --assignee-object-id $me --assignee-principal-type User --role 'Key Vault Secrets Officer' --scope $kvId -o none)
[void](AzTry role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role 'Key Vault Secrets User' --scope $kvId -o none)
Start-Sleep 30   # propagación de RBAC
if (-not $out.azureInboundApiKey) {
  $out.azureInboundApiKey = if ($dotenv['AZURE_INBOUND_API_KEY']) { $dotenv['AZURE_INBOUND_API_KEY'] } else { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ }) }
}
if (-not $out.operatorToken) { $out.operatorToken = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ }) }
$secrets = @{ 'cosmos-connection' = $cosmosConn; 'servicebus-connection' = $sbConn; 'operator-token' = $out.operatorToken }
foreach ($k in $secrets.Keys) { Az keyvault secret set --vault-name $n.kv -n $k --value $secrets[$k] -o none }

Step "7. Communication Services + Email (si falla, queda email simulado)"
$acsConn = ''; $acsSender = ''
try {
  if (-not (AzTry communication show -n $n.acs -g $RG -o none)) { Az communication create -n $n.acs -g $RG --location global --data-location unitedstates -o none }
  if (-not (AzTry communication email show -n $n.email -g $RG -o none)) { Az communication email create -n $n.email -g $RG --location global --data-location unitedstates -o none }
  if (-not (AzTry communication email domain show --domain-name AzureManagedDomain --email-service-name $n.email -g $RG -o none)) {
    Az communication email domain create --domain-name AzureManagedDomain --email-service-name $n.email -g $RG --location global --domain-management AzureManaged -o none
  }
  $domId = (Az communication email domain show --domain-name AzureManagedDomain --email-service-name $n.email -g $RG --query id -o tsv).Trim()
  $fromDomain = (Az communication email domain show --domain-name AzureManagedDomain --email-service-name $n.email -g $RG --query mailFromSenderDomain -o tsv).Trim()
  Az communication update -n $n.acs -g $RG --linked-domains $domId -o none
  $acsConn = (Az communication list-key --name $n.acs -g $RG --query primaryConnectionString -o tsv).Trim()
  $acsSender = "DoNotReply@$fromDomain"
  Az keyvault secret set --vault-name $n.kv -n 'acs-connection' --value $acsConn -o none
  Write-Host "ACS listo. Remitente: $acsSender"
} catch {
  Write-Host "ACS no se pudo automatizar ($($_.Exception.Message.Split("`n")[0])). Email queda SIMULADO; se puede completar desde el portal luego." -ForegroundColor Yellow
}

Step "8. App settings de la Function App"
$settings = @(
  "COSMOS=$(Setting 'cosmos-connection' $cosmosConn)", "SB=$(Setting 'servicebus-connection' $sbConn)",
  "OPERATOR_TOKEN=$(Setting 'operator-token' $out.operatorToken)", "COSMOS_DB=pulsored"
)
$settings += External-Settings
if ($acsConn) { $settings += "ACS_CONNECTION_STRING=$(Setting 'acs-connection' $acsConn)"; $settings += "ACS_SENDER=$acsSender" }
Set-AppSettings $settings

if (-not $SkipBackend) {
  Step "9. Publicar backend"
  if (-not (Get-Command func -ErrorAction SilentlyContinue)) {   # instalado pero la terminal no tiene el PATH nuevo
    $env:Path += ';C:\Program Files\Microsoft\Azure Functions Core Tools'
  }
  Push-Location (Join-Path $root 'backend')
  try {
    $ErrorActionPreference = 'Continue'
    & func azure functionapp publish $n.func --python
    $code = $LASTEXITCODE; $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { throw 'func publish falló' }
  } finally { Pop-Location }
}

Step "10. Frontend (Static Web Apps, plan Free)"
$swaHost = ''
if (-not $SkipFrontend) {
  if (-not (AzTry staticwebapp show -n $n.swa -g $RG -o none)) { Az staticwebapp create -n $n.swa -g $RG -l $Region --sku Free -o none }
  $swaHost = (Az staticwebapp show -n $n.swa -g $RG --query defaultHostname -o tsv).Trim()
  $tmp = Join-Path $env:TEMP "pulsored-web-$Suffix"
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  Copy-Item (Join-Path $root 'frontend') $tmp -Recurse
  [IO.File]::WriteAllText((Join-Path $tmp 'config.json'), (@{ apiBase = "https://$funcHost" } | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
  $tok = (Az staticwebapp secrets list -n $n.swa -g $RG --query properties.apiKey -o tsv).Trim()
  $ErrorActionPreference = 'Continue'
  & swa deploy $tmp --deployment-token $tok --env production
  $code = $LASTEXITCODE; $ErrorActionPreference = 'Stop'
  if ($code -ne 0) { throw 'swa deploy falló' }
  [void](AzTry functionapp cors add -n $n.func -g $RG --allowed-origins "https://$swaHost" -o none)
}

Step "11. Salidas"
$out.resourceGroup = $RG; $out.region = $Region; $out.suffix = $Suffix; $out.names = $n
$out.apiBase = "https://$funcHost"; $out.siteUrl = if ($swaHost) { "https://$swaHost" } else { '' }
$out.infraEventsUrl = "https://$funcHost/api/events/infra"; $out.azureBaseUrl = "https://$funcHost/api"
$out.acsSender = $acsSender
($out | ConvertTo-Json -Depth 5) | Set-Content $outFile -Encoding utf8

Write-Host "`nLISTO." -ForegroundColor Green
Write-Host "API:        https://$funcHost/api/health"
Write-Host "Sitio:      $($out.siteUrl)"
Write-Host "Eventos AWS: $($out.infraEventsUrl)   (X-API-Key en infra\outputs.local.json -> azureInboundApiKey)"
Write-Host "Para P1 (AWS): SSM /pulsored/endpoints/azure = $($out.azureBaseUrl)  y  outbound.azure_api_key = azureInboundApiKey"
Write-Host "Siguiente:  .\infra\smoke_test.ps1"
