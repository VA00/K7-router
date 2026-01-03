$inputArgs = Get-ChildItem route_*.gpx | ForEach-Object { "-i", "gpx", "-f", $_.Name }
if (-not $inputArgs) {
    Write-Error "No route_*.gpx files found in current directory"
    exit 1
}
& "C:\Program Files\GPSBabel\gpsbabel.exe" @inputArgs -o gpx -F merged_routes.gpx