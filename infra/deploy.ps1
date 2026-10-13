# Deploy the match centre and newsroom server to Azure Container Apps (builds the image in Azure; no local Docker needed).
#
#   ./infra/deploy.ps1            # after infra/setup.ps1; re-run to publish a new version
#
# The app signs in to Foundry with its own managed identity (no keys anywhere), which gets the "Foundry User" role on the resource.
param(
    [string]$Rg = "rg-matchday-newsroom",
    [string]$Location = "eastus2",
    [string]$App = "matchday-newsroom",
    [string]$Account = "matchday-newsroom-$((az account show --query id -o tsv).Substring(0, 6))",
    [string]$Project = "newsroom",
    [string]$Model = "gpt-4.1-mini"
)
function Ok { if ($LASTEXITCODE) { Write-Host "Failed (see the error above); fix it and re-run." -ForegroundColor Red; exit 1 } }

foreach ($ns in "Microsoft.App", "Microsoft.OperationalInsights", "Microsoft.ContainerRegistry") { az provider register --namespace $ns --wait; Ok }
$endpoint = "https://$Account.services.ai.azure.com/api/projects/$Project"
$resId = az cognitiveservices account show -n $Account -g $Rg --query id -o tsv; Ok
az containerapp up -n $App -g $Rg -l $Location --source . --ingress external --target-port 8080 --env-vars "FOUNDRY_PROJECT_ENDPOINT=$endpoint" "FOUNDRY_MODEL=$Model" "ASK_SLOTS=2" "SPEECH_ENDPOINT=https://$Account.cognitiveservices.azure.com" "SPEECH_RESOURCE_ID=$resId"; Ok
az containerapp update -n $App -g $Rg --min-replicas 0 --max-replicas 1 -o none; Ok  # scales to zero when nobody is watching
$principal = az containerapp identity assign -n $App -g $Rg --system-assigned --query principalId -o tsv; Ok
$scope = az cognitiveservices account show -n $Account -g $Rg --query id -o tsv; Ok
az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role "Foundry User" --scope $scope -o none; Ok
$fqdn = az containerapp show -n $App -g $Rg --query properties.configuration.ingress.fqdn -o tsv; Ok
Write-Host "`nLive at https://$fqdn  (the role can take a few minutes before the first answer works)"
