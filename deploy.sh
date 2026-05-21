#!/bin/bash

###############################################################################
# Falak AI Backend Deployment Script
#
# This script automates the deployment process for the Laravel backend.
# It handles dependency installation, migrations, cache clearing, and more.
#
# Usage: ./deploy.sh [environment]
# Example: ./deploy.sh production
###############################################################################

set -e  # Exit on any error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get environment from first argument, default to production
ENVIRONMENT="${1:-production}"

# Print colored message
print_message() {
    echo -e "${GREEN}==>${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}Warning:${NC} $1"
}

print_error() {
    echo -e "${RED}Error:${NC} $1"
}

print_section() {
    echo -e "\n${BLUE}###############################################################################${NC}"
    echo -e "${BLUE}# $1${NC}"
    echo -e "${BLUE}###############################################################################${NC}\n"
}

# Check if we're in the correct directory
if [ ! -f "artisan" ]; then
    print_error "artisan file not found. Please run this script from the falak_ai_backend directory."
    exit 1
fi

print_section "Starting Deployment Process for ${ENVIRONMENT} environment"

# Enable maintenance mode
print_message "Putting application into maintenance mode..."
php artisan down || true

# Function to handle cleanup on exit
cleanup() {
    if [ $? -ne 0 ]; then
        print_error "Deployment failed! Bringing application back online..."
    else
        print_message "Bringing application out of maintenance mode..."
    fi
    php artisan up
}

trap cleanup EXIT

# Pull latest changes from git (if in a git repository)
if [ -d ".git" ]; then
    print_section "Pulling Latest Changes"
    print_message "Fetching latest code from repository..."
    git pull origin $(git branch --show-current)
else
    print_warning "Not a git repository. Skipping git pull."
fi

# Install/Update Composer dependencies
print_section "Installing Composer Dependencies"
print_message "Running composer install (optimized for ${ENVIRONMENT})..."
if [ "$ENVIRONMENT" = "production" ]; then
    composer install --no-dev --optimize-autoloader --no-interaction
else
    composer install --optimize-autoloader --no-interaction
fi

# Install/Update NPM dependencies
print_section "Installing NPM Dependencies"
if [ -f "package.json" ]; then
    print_message "Installing NPM packages..."
    npm ci --legacy-peer-deps || npm install --legacy-peer-deps

    print_message "Building frontend assets..."
    npm run build
else
    print_warning "package.json not found. Skipping NPM install."
fi

# Clear and cache configuration
print_section "Optimizing Application"
print_message "Clearing configuration cache..."
php artisan config:clear

print_message "Clearing route cache..."
php artisan route:clear

print_message "Clearing view cache..."
php artisan view:clear

print_message "Clearing application cache..."
php artisan cache:clear

# Run database migrations
print_section "Running Database Migrations"
print_message "Executing migrations..."
php artisan migrate --force

# Optimize application for production
if [ "$ENVIRONMENT" = "production" ]; then
    print_section "Production Optimizations"

    print_message "Caching configuration..."
    php artisan config:cache

    print_message "Caching routes..."
    php artisan route:cache

    print_message "Caching views..."
    php artisan view:cache

    print_message "Caching events..."
    php artisan event:cache
fi

# Clear and optimize autoloader
print_message "Optimizing autoloader..."
composer dump-autoload --optimize

# Set proper permissions
print_section "Setting File Permissions"
print_message "Setting storage and cache permissions..."

if [ -d "storage" ]; then
    chmod -R 775 storage
    chmod -R 775 bootstrap/cache
    print_message "Storage permissions set"
fi

# Restart queue workers (if using supervisor or similar)
print_section "Restarting Services"
print_message "Restarting queue workers..."
php artisan queue:restart

# Clear opcache (if available)
if command -v cachetool &> /dev/null; then
    print_message "Clearing OPcache..."
    cachetool opcache:reset
fi

# Run health check
print_section "Health Check"
print_message "Running post-deployment health check..."

# Check if database is accessible
if php artisan db:show &> /dev/null; then
    print_message "✓ Database connection successful"
else
    print_warning "✗ Database connection check failed"
fi

# Check storage link
if [ -L "public/storage" ]; then
    print_message "✓ Storage link exists"
else
    print_warning "✗ Storage link missing. Creating..."
    php artisan storage:link
fi

print_section "Deployment Summary"
echo -e "${GREEN}✓ Deployment completed successfully!${NC}"
echo ""
echo "Environment: ${ENVIRONMENT}"
echo "Deployed at: $(date)"
echo "Git commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'N/A')"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Verify application is working correctly"
echo "2. Monitor logs for any errors: tail -f storage/logs/laravel.log"
echo "3. Check queue workers are running: php artisan queue:monitor"
echo ""

exit 0
