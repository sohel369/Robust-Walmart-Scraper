# ==============================================================================
# Walmart Scraper Microsoft Azure PowerShell Deployment Script
# Automatically builds, deploys, and configures twice-per-week execution in Azure
# ==============================================================================

param(
    [string]$ResourceGroup = "rg-walmart-scraper-prod",
    [string]$Location = "eastus",
    [string]$AcrName = ("acrwalmartprod" + (Get-Random -Minimum 1000 -Maximum 9999)),
    [string]$ImageName = "walmart-scraper",
    [string]$Tag = "v1",
    [string]$AciName = "aci-walmart-scraper-job"
)

$ErrorActionPreference = "Stop"

Write-Host "=== 1. Creating Azure Resource Group: $ResourceGroup ===" -ForegroundColor Cyan
az group create --name $ResourceGroup --location $Location

Write-Host "=== 2. Creating Azure Container Registry: $AcrName ===" -ForegroundColor Cyan
az acr create --resource-group $ResourceGroup --name $AcrName --sku Basic --admin-enabled true

Write-Host "=== 3. Creating Persistent Azure Storage & File Share ===" -ForegroundColor Cyan
$StorageAccountName = ("sawalmart" + (Get-Random -Minimum 1000 -Maximum 9999))
az storage account create --resource-group $ResourceGroup --name $StorageAccountName --location $Location --sku Standard_LRS
$StorageAccountKey = (az storage account keys list --resource-group $ResourceGroup --account-name $StorageAccountName --query "[0].value" --output tsv)
az storage share create --name "walmart-scraper-share" --account-name $StorageAccountName --account-key $StorageAccountKey

Write-Host "=== 4. Building and Pushing Container Image to ACR ===" -ForegroundColor Cyan
az acr build --registry $AcrName --image "$($ImageName):$($Tag)" -f azure/Dockerfile .

$AcrLoginServer = (az acr show --name $AcrName --query loginServer --output tsv)
$AcrUsername = (az acr credential show --name $AcrName --query username --output tsv)
$AcrPassword = (az acr credential show --name $AcrName --query "passwords[0].value" --output tsv)

Write-Host "=== 5. Launching Azure Container Instance with Persistent Mount ===" -ForegroundColor Cyan
az container create `
  --resource-group $ResourceGroup `
  --name $AciName `
  --image "$AcrLoginServer/$($ImageName):$($Tag)" `
  --registry-login-server $AcrLoginServer `
  --registry-username $AcrUsername `
  --registry-password $AcrPassword `
  --cpu 2 `
  --memory 4 `
  --restart-policy Never `
  --azure-file-volume-account-name $StorageAccountName `
  --azure-file-volume-account-key $StorageAccountKey `
  --azure-file-volume-share-name "walmart-scraper-share" `
  --azure-file-volume-mount-path "/app/data" `
  --environment-variables `
      WALMART_PROXY_URL="$env:WALMART_PROXY_URL" `
      AZURE_STORAGE_CONNECTION_STRING="$env:AZURE_STORAGE_CONNECTION_STRING" `
      AZURE_STORAGE_CONTAINER_NAME="walmart-scraper-data"

Write-Host "=== Deployment Succeeded! Container job active in Azure. ===" -ForegroundColor Green
