# Deploy the match centre and newsroom server to Azure Container Apps. The image is built by GitHub Actions (.github/workflows/image.yml)
# and pulled from GitHub Container Registry (a public package), because free-trial subscriptions block ACR Tasks; no local Docker needed.
#
#   ./infra/deploy.ps1            # after infra/setup.ps1; re-run to publish a new version
#
# Two apps share one Container Apps environment:
#   $App       the match centre and newsroom (external ingress); signs in to Foundry with its own managed identity (no keys anywhere),
#              which gets the "Foundry User" role on the resource
#   $App-sim   the simulation workers (internal ingress only): each what-if replay is one request, and the workers scale out with the
#              requests (2 at a time each, up to 12 replicas) and back to zero, so a what-if takes about as long as one replay
param(
    [string]$Rg = "rg-matchday-newsroom",
    [string]$Location = "eastus2",
    [string]$App = "football-kaleidoscope",
    [string]$Account = "matchday-newsroom-$((az account show --query id -o tsv).Substring(0, 6))",
    [string]$Project = "newsroom",
    [string]$Model = "gpt-4.1-mini",
    [string]$StrongModel = "gpt-4.1",  # the writer and the fact-checkers (a deployment of its own in the Foundry project)
    [string]$Image = "ghcr.io/nano-233/matchday-newsroom:latest"
)
function Ok { if ($LASTEXITCODE) { Write-Host "Failed (see the error above); fix it and re-run." -ForegroundColor Red; exit 1 } }

foreach ($ns in "Microsoft.App", "Microsoft.OperationalInsights") { az provider register --namespace $ns --wait; Ok }
$endpoint = "https://$Account.services.ai.azure.com/api/projects/$Project"
$resId = az cognitiveservices account show -n $Account -g $Rg --query id -o tsv; Ok
$aiConn = az monitor app-insights component show -g $Rg --app "$Account-insights" --query connectionString -o tsv --only-show-errors; Ok  # tracing (setup.ps1 made it)
az containerapp up -n $App -g $Rg -l $Location --image $Image --ingress external --target-port 8080 --env-vars "FOUNDRY_PROJECT_ENDPOINT=$endpoint" "FOUNDRY_MODEL=$Model" "FOUNDRY_STRONG_MODEL=$StrongModel" "ASK_SLOTS=2" "SPEECH_ENDPOINT=https://$Account.cognitiveservices.azure.com" "SPEECH_RESOURCE_ID=$resId" "APPLICATIONINSIGHTS_CONNECTION_STRING=$aiConn"; Ok
$envName = az containerapp show -n $App -g $Rg --query properties.managedEnvironmentId -o tsv; Ok
az containerapp up -n "$App-sim" -g $Rg -l $Location --environment $envName --image $Image --ingress internal --target-port 8080 --env-vars "SIM_WORKER=1" "SIM_WORKERS=2"; Ok
az containerapp update -n "$App-sim" -g $Rg --cpu 2 --memory 4Gi --min-replicas 0 --max-replicas 12 --scale-rule-name http --scale-rule-type http --scale-rule-http-concurrency 2 -o none; Ok
$simFqdn = az containerapp show -n "$App-sim" -g $Rg --query properties.configuration.ingress.fqdn -o tsv; Ok
az containerapp update -n $App -g $Rg --min-replicas 0 --max-replicas 1 --cpu 1 --memory 2Gi --set-env-vars "FANOUT_URL=https://$simFqdn" -o none; Ok  # scales to zero when nobody is watching
$principal = az containerapp identity assign -n $App -g $Rg --system-assigned --query principalId -o tsv; Ok
$scope = az cognitiveservices account show -n $Account -g $Rg --query id -o tsv; Ok
az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role "Foundry User" --scope $scope -o none; Ok
$fqdn = az containerapp show -n $App -g $Rg --query properties.configuration.ingress.fqdn -o tsv; Ok
Write-Host "`nLive at https://$fqdn  (the role can take a few minutes before the first answer works)"
