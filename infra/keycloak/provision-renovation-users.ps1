# Provision renovation-project demo users in the local Keycloak realm.
#
# Idempotent: users are created once, then passwords are (re)set every run —
# dev/demo only. Mirrors the app_users seeded by
# back/src/scripts/seed_database.py (RENOVATION_USERS): the three responsibility
# blocks are company directorates (tech / ksir / mto) and the demo accounts are
# placeholders to be replaced by real employees.
#
# LOCAL/DEMO ONLY — changes the local Keycloak realm, never CI or production.
#
# Usage: set RENOVATION_SEED_PASSWORD to a demo password, then run
#        powershell -ExecutionPolicy Bypass -File infra/keycloak/provision-renovation-users.ps1
#        [-CleanupLegacy]   # also delete the pre-directorate demo usernames
#
# RENOVATION_SEED_PASSWORD is REQUIRED and has no default: the script fails
# before any kcadm call when it is absent. This is a local/demo Keycloak
# password; never reuse the app's E2E_TEST_PASSWORD (different stand/contract).
# Other env overrides: KEYCLOAK_ADMIN_USERNAME, KEYCLOAK_ADMIN_PASSWORD.
param([switch]$CleanupLegacy)
$ErrorActionPreference = "Stop"

$Container = "keycloak"
$Realm = "reportcollector"
$Server = "http://localhost:8080"
$AdminUser = if ($env:KEYCLOAK_ADMIN_USERNAME) { $env:KEYCLOAK_ADMIN_USERNAME } else { "admin" }
$AdminPassword = if ($env:KEYCLOAK_ADMIN_PASSWORD) { $env:KEYCLOAK_ADMIN_PASSWORD } else { "admin" }
$Password = $env:RENOVATION_SEED_PASSWORD
if (-not $Password) {
    throw "RENOVATION_SEED_PASSWORD is required for local/demo Keycloak provisioning; no default password is used."
}

function Invoke-Kc {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$KcArgs)
    docker exec $Container /opt/keycloak/bin/kcadm.sh @KcArgs
}

# username -> @{ LastName; FirstName; Email } (block owners are the directorates;
# department users use the asset code as the first name so the display name
# stays informative). Mirrors seed_database.py RENOVATION_USERS.
$Users = @{
    vasilenko = @{ LastName = "Василенко";            FirstName = "Екатерина"; Email = "vasilenko@ruschem.ru" }
    rogov     = @{ LastName = "Рогов";                FirstName = "Максим";    Email = "rogov@ruschem.ru" }
    # Block owners represent a directorate, not a person — but Keycloak's user
    # profile requires a non-blank first name, otherwise the login is stopped by
    # the VERIFY_PROFILE required action. Placeholder until real employees exist.
    tech      = @{ LastName = "Техническая дирекция"; FirstName = "Директор"; Email = "tech@ruschem.ru" }
    ksir      = @{ LastName = "Дирекция КСиР";        FirstName = "Директор"; Email = "ksir@ruschem.ru" }
    mto       = @{ LastName = "Дирекция МТО";         FirstName = "Директор"; Email = "mto@ruschem.ru" }
    "tech-vos"  = @{ LastName = "Техническая дирекция"; FirstName = "ВОС";  Email = "tech-vos@ruschem.ru" }
    "ksir-vos"  = @{ LastName = "Дирекция КСиР";        FirstName = "ВОС";  Email = "ksir-vos@ruschem.ru" }
    "mto-vos"   = @{ LastName = "Дирекция МТО";         FirstName = "ВОС";  Email = "mto-vos@ruschem.ru" }
    "tech-snhz" = @{ LastName = "Техническая дирекция"; FirstName = "СНХЗ"; Email = "tech-snhz@ruschem.ru" }
    "ksir-snhz" = @{ LastName = "Дирекция КСиР";        FirstName = "СНХЗ"; Email = "ksir-snhz@ruschem.ru" }
    "mto-snhz"  = @{ LastName = "Дирекция МТО";         FirstName = "СНХЗ"; Email = "mto-snhz@ruschem.ru" }
    "tech-bsk"  = @{ LastName = "Техническая дирекция"; FirstName = "БСК";  Email = "tech-bsk@ruschem.ru" }
    "ksir-bsk"  = @{ LastName = "Дирекция КСиР";        FirstName = "БСК";  Email = "ksir-bsk@ruschem.ru" }
    "mto-bsk"   = @{ LastName = "Дирекция МТО";         FirstName = "БСК";  Email = "mto-bsk@ruschem.ru" }
}

Invoke-Kc config credentials --server $Server --realm master --user $AdminUser --password $AdminPassword | Out-Null

function Get-UserId {
    param([string]$Username)
    # Keycloak's username query is a prefix/infix match, so `mto` also hits
    # `mto-bsk`; require an exact username match.
    $raw = Invoke-Kc get users -r $Realm --query "username=$Username" --fields "id,username"
    $parsed = ($raw -join "`n") | ConvertFrom-Json -ErrorAction SilentlyContinue
    foreach ($entry in @($parsed)) {
        if ($entry.username -eq $Username) { return $entry.id }
    }
    return ""
}

foreach ($username in $Users.Keys) {
    $profile = $Users[$username]
    $userId = Get-UserId $username
    if (-not $userId) {
        Invoke-Kc create users -r $Realm -s "username=$username" -s "email=$($profile.Email)" `
            -s "firstName=$($profile.FirstName)" -s "lastName=$($profile.LastName)" -s "enabled=true" | Out-Null
        Write-Host "created $username"
    }
    else {
        Invoke-Kc update "users/$userId" -r $Realm -s "email=$($profile.Email)" `
            -s "firstName=$($profile.FirstName)" -s "lastName=$($profile.LastName)" | Out-Null
        Write-Host "updated $username"
    }
    Invoke-Kc set-password -r $Realm --username $username --new-password $Password | Out-Null
}

# Legacy demo usernames replaced by the directorate scheme. Only touched with
# -CleanupLegacy (they may still be referenced by old app_users rows otherwise).
if ($CleanupLegacy) {
    $Legacy = @("tashpaev", "borodiichuk", "babaeva", "td-vos", "opof-vos", "umto-vos", `
        "td-snhz", "opof-snhz", "umto-snhz", "td-bsk", "opof-bsk", "umto-bsk")
    foreach ($username in $Legacy) {
        $userId = Get-UserId $username
        if ($userId) {
            Invoke-Kc delete "users/$userId" -r $Realm | Out-Null
            Write-Host "deleted legacy $username"
        }
    }
}

Write-Host "Keycloak renovation users ready (password supplied via RENOVATION_SEED_PASSWORD)."
