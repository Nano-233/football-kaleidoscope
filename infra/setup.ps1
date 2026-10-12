# One-time Azure setup for the newsroom: a Foundry resource, a project in it, a model deployment, and data-plane access for you.
#
#   az login
#   ./infra/setup.ps1                       # defaults below; override with -Location swedencentral etc.
#
# Prints the two environment variables newsroom/clients.py reads. Stops at the first failure; safe to re-run.
param(
    [string]$Rg = "rg-matchday-newsroom",
    [string]$Location = "eastus2",
    [string]$Account = "matchday-newsroom-$((az account show --query id -o tsv).Substring(0, 6))",  # custom domain must be globally unique
    [string]$Project = "newsroom",
    [string]$Model = "gpt-4.1-mini",
    [string]$ModelVersion = "2025-04-14",
    [int]$Capacity = 50                     # thousands of tokens per minute, GlobalStandard (pay per token)
)
function Ok { if ($LASTEXITCODE) { Write-Host "Failed (see the error above); fix it and re-run." -ForegroundColor Red; exit 1 } }  # PowerShell 5.1 does not stop on native-command errors

# A new subscription has to register the resource provider once (takes a minute or two).
az provider register --namespace Microsoft.CognitiveServices --wait; Ok
az group create -n $Rg -l $Location -o none; Ok
az cognitiveservices account create -n $Account -g $Rg -l $Location --kind AIServices --sku S0 --custom-domain $Account --allow-project-management --yes -o none; Ok
az cognitiveservices account project create -n $Account -g $Rg --project-name $Project -l $Location -o none; Ok
az cognitiveservices account deployment create -n $Account -g $Rg --deployment-name $Model --model-name $Model --model-version $ModelVersion --model-format OpenAI --sku-name GlobalStandard --sku-capacity $Capacity -o none; Ok

# DefaultAzureCredential uses your az login; it needs a data-plane role on the resource, not just Owner of the subscription.
$me = az ad signed-in-user show --query id -o tsv; Ok
$scope = az cognitiveservices account show -n $Account -g $Rg --query id -o tsv; Ok
# "Foundry User" (formerly "Azure AI User") covers the Responses API and agents; the OpenAI role alone only covers chat completions.
foreach ($role in "Foundry User") { az role assignment create --assignee-object-id $me --assignee-principal-type User --role $role --scope $scope -o none; Ok }

Write-Host "`nDone. Set these (PowerShell), then: python scripts/run_newsroom.py out/match_1.json"
Write-Host "`$env:FOUNDRY_PROJECT_ENDPOINT = `"https://$Account.services.ai.azure.com/api/projects/$Project`""
Write-Host "`$env:FOUNDRY_MODEL = `"$Model`""
