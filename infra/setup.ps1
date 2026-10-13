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

# Tracing: Application Insights (on a Log Analytics workspace), connected to the project so the Foundry portal's Tracing page shows
# every agent run, model call and tool call (the app sends them through FoundryChatClient.configure_azure_monitor).
foreach ($ns in "Microsoft.OperationalInsights", "Microsoft.Insights") { az provider register --namespace $ns --wait; Ok }
az config set extension.use_dynamic_install=yes_without_prompt -o none 2>$null
$ws = az monitor log-analytics workspace create -g $Rg -n "$Account-logs" -l $Location --query id -o tsv; Ok
$ai = az monitor app-insights component create -g $Rg --app "$Account-insights" -l $Location --workspace $ws --query "{id:id,cs:connectionString}" -o json | ConvertFrom-Json; Ok
$body = @{ properties = @{ category = "AppInsights"; target = $ai.id; authType = "ApiKey"; isSharedToAll = $false; credentials = @{ key = $ai.cs }; metadata = @{ ApiType = "Azure"; ResourceId = $ai.id } } } | ConvertTo-Json -Depth 5 -Compress
$tmp = New-TemporaryFile; Set-Content $tmp $body -Encoding ascii
az rest --method put --url "https://management.azure.com$scope/projects/$Project/connections/$Account-insights?api-version=2025-06-01" --body "@$tmp" -o none; Ok
Remove-Item $tmp

Write-Host "`nDone. Set these (PowerShell), then: python scripts/run_newsroom.py out/match_1.json"
Write-Host "`$env:FOUNDRY_PROJECT_ENDPOINT = `"https://$Account.services.ai.azure.com/api/projects/$Project`""
Write-Host "`$env:FOUNDRY_MODEL = `"$Model`""
