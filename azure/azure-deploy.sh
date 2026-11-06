#!/bin/bash
set -e

# ==============================================================================
# Walmart Scraper Microsoft Azure Deployment Script
# Automatically builds, deploys, and configures twice-per-week execution in Azure
# ==============================================================================

RESOURCE_GROUP="rg-walmart-scraper-prod"
LOCATION="eastus"
ACR_NAME="acrwalmartscraperprod$RANDOM"
IMAGE_NAME="walmart-scraper"
TAG="v1"
ACI_NAME="aci-walmart-scraper-job"

echo "=== 1. Creating Resource Group in Azure: $RESOURCE_GROUP ==="
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"

echo "=== 2. Creating Azure Container Registry (ACR): $ACR_NAME ==="
az acr create --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --sku Basic --admin-enabled true

echo "=== 3. Creating Persistent Azure Storage & File Share ==="
STORAGE_ACCOUNT_NAME="sawalmart$RANDOM"
az storage account create --resource-group "$RESOURCE_GROUP" --name "$STORAGE_ACCOUNT_NAME" --location "$LOCATION" --sku Standard_LRS
STORAGE_ACCOUNT_KEY=$(az storage account keys list --resource-group "$RESOURCE_GROUP" --account-name "$STORAGE_ACCOUNT_NAME" --query "[0].value" --output tsv)
az storage share create --name "walmart-scraper-share" --account-name "$STORAGE_ACCOUNT_NAME" --account-key "$STORAGE_ACCOUNT_KEY"

echo "=== 4. Building and Pushing Docker Image to ACR ==="
az acr build --registry "$ACR_NAME" --image "$IMAGE_NAME:$TAG" -f azure/Dockerfile .

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer --output tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" --output tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username --output tsv)

echo "=== 5. Deploying Container Job to Azure Container Instances with Persistent Share ==="
az container create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACI_NAME" \
  --image "$ACR_LOGIN_SERVER/$IMAGE_NAME:$TAG" \
  --registry-login-server "$ACR_LOGIN_SERVER" \
  --registry-username "$ACR_USERNAME" \
  --registry-password "$ACR_PASSWORD" \
  --cpu 2 \
  --memory 4 \
  --restart-policy Never \
  --azure-file-volume-account-name "$STORAGE_ACCOUNT_NAME" \
  --azure-file-volume-account-key "$STORAGE_ACCOUNT_KEY" \
  --azure-file-volume-share-name "walmart-scraper-share" \
  --azure-file-volume-mount-path "/app/data" \
  --environment-variables \
      WALMART_PROXY_URL="$WALMART_PROXY_URL" \
      AZURE_STORAGE_CONNECTION_STRING="$AZURE_STORAGE_CONNECTION_STRING" \
      AZURE_STORAGE_CONTAINER_NAME="walmart-scraper-data"

echo "=== Azure Deployment Completed Successfully! ==="
echo "ACR Registry: $ACR_LOGIN_SERVER"
echo "Container: $ACI_NAME"
