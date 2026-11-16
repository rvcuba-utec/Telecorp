<#  Borra TODO el grupo de recursos (irreversible). Solo para cuando termine la evaluación.  #>
param([string]$RG = 'rg-pulsored', [switch]$Yes)
if (-not $Yes) { Write-Host "Esto borra el grupo '$RG' y todo su contenido. Reejecuta con -Yes para confirmar."; exit 1 }
& az group delete -n $RG --yes --no-wait
Write-Host "Borrado en curso (sin esperar)."
