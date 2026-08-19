# P# Provision renovation-project demo users in the local Keycloak realm.
#
# Idempotent: users are created once, then passwords are (re)set every run —
# dev/demo only. Mirrors the app_users seeded by
# back/src/scripts/seed_database.py (RENOVATION_USERS).
#
# Usage: powershell -ExecutionPolicy Bypass -File infra/keycloak/provision-renovation-users.ps1
# Env overrides: KEYCLOAK_ADMIN_USERNAME, KEYCLOAK_ADMIN_PASSWORD, RENOVATION_SEED_PASSWORD.
$ErrorActionPreference = "Stop"

$Container = "keycloak"
$Realm = "reportcollector"
$Server = "http://localhost:8080"
$AdminUser = if ($env:KEYCLOAK_ADMIN_USERNAME) { $env:KEYCLOAK_ADMIN_USERNAME } else { "admin" }
$AdminPassword = if ($env:KEYCLOAK_ADMIN_PASSWORD) { $env:KEYCLOAK_ADMIN_PASSWORD } else { "admin" }
$Password = if ($env:RENOVATION_SEED_PASSWORD) { $env:RENOVATION_SEED_PASSWORD } else { "TestPassword123" }

function Invoke-Kc {
    param([string[]]$KcArgs)
    docker exec $Container /opt/keycloak/bin/kcadm.sh @KcArgs
}

# username -> @{ LastName; FirstName; Email } (department users use the asset
# code as the first name so the display name stays informative).
$Users = @{
    vasilenko   = @{ LastName = "Василенко";                       FirstName = "Екатерина"; Email = "vasilenko@ruschem.ru" }
    rogov       = @{ LastName = "Рогов";                           FirstName = "Максим";    Email = "rogov@ruschem.ru" }
    tashpaev    = @{ LastName = "Ташпаев";                         FirstName = "Алексей";   Email = "tashpaev@ruschem.ru" }
    borodiichuk = @{ LastName = "Бородийчук";                      FirstName = "Алексей";   Email = "borodiichuk.av@ruschem.ru" }
    babaeva     = @{ LastName = "Бабаева";                         FirstName = "Ирена";     Email = "babaeva.ir@ruschem.ru" }
    "td-vos"    = @{ LastName = "Технический департамент";         FirstName = "ВОС";       Email = "td-vos@ruschem.ru" }
    "opof-vos"  = @{ LastName = "Производственно-финансовый отдел"; FirstName = "ВОС";      Email = "opof-vos@ruschem.ru" }
    "umto-vos"  = @{ LastName = "Управление МТО";                  FirstName = "ВОС";       Email = "umto-vos@ruschem.ru" }
    "td-snhz"   = @{ LastName = "Технический департамент";         FirstName = "СНХЗ";      Email = "td-snhz@ruschem.ru" }
    "opof-snhz" = @{ LastName = "Производственно-финансовый отдел"; FirstName = "СНХЗ";     Email = "opof-snhz@ruschem.ru" }
    "umto-snhz" = @{ LastName = "Управление МТО";                  FirstName = "СНХЗ";      Email = "umto-snhz@ruschem.ru" }
    "td-bsk"    = @{ LastName = "Технический департамент";         FirstName = "БСК";       Email = "td-bsk@ruschem.ru" }
    "opof-bsk"  = @{ LastName = "Производственно-финансовый отдел"; FirstName = "БСК";      Email = "opof-bsk@ruschem.ru" }
    "umto-bsk"  = @{ LastName = "Управление МТО";                  FirstName = "БСК";       Email = "umto-bsk@ruschem.ru" }
}

Invoke-Kc config credentials --server $Server --realm master --user $AdminUser --password $AdminPassword | Out-Null

foreach ($username in $Users.Keys) {
    $profile = $Users[$username]
    $list = Invoke-Kc get users -r $Realm --query "username=$username" --fields id
    $match = $list | Select-String '"id"\s*:\s*"([^"]+)"'
    $userId = if ($match) { $match.Matches.Groups[1].Value } else { "" }
    if (-not $userId) {
        Invoke-Kc create users -r $Realm -s "username=$username" -s "email=$($profile.Email)" `
            -s "firstName=$($profile.FirstName)" -s "lastName=$($profile.LastName)" -s "enabled=true" | Out-Null
        Write-Host "created $username"
    }
    else {
        Invoke-Kc update users -r $Realm -u $userId -s "email=$($profile.Email)" `
            -s "firstName=$($profile.FirstName)" -s "lastName=$($profile.LastName)" | Out-Null
        Write-Host "updated $username"
    }
    Invoke-Kc set-password -r $Realm --username $username --new-password $Password | Out-Null
}

Write-Host "Keycloak renovation users ready (password: $Password)."
