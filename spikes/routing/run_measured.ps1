# Runs a command (e.g. motis import) and samples its memory every few seconds.
# Usage: powershell -File run_measured.ps1 -Exe <path> -Arguments "<args>" -WorkDir <dir> -Log <csv> [-IntervalSec 2]
# Writes a CSV of samples and prints a summary: wall time, max working set, max private bytes,
# and the OS-tracked PeakWorkingSet64 (captured just before exit).
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$Arguments = "",
    [Parameter(Mandatory = $true)][string]$WorkDir,
    [Parameter(Mandatory = $true)][string]$Log,
    [double]$IntervalSec = 2,
    [string]$StdOut = "",
    [string]$StdErr = ""
)

$psi = @{ FilePath = $Exe; WorkingDirectory = $WorkDir; PassThru = $true; NoNewWindow = $true }
if ($Arguments) { $psi.ArgumentList = $Arguments }
if ($StdOut) { $psi.RedirectStandardOutput = $StdOut }
if ($StdErr) { $psi.RedirectStandardError = $StdErr }

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$p = Start-Process @psi
$null = $p.Handle  # caching the handle is required for ExitCode to be available later
"t_s;working_set_mb;private_mb;peak_ws_mb" | Out-File -Encoding utf8 $Log
$maxWs = 0; $maxPriv = 0; $peak = 0
while (-not $p.HasExited) {
    try {
        $p.Refresh()
        $ws = $p.WorkingSet64 / 1MB; $pr = $p.PrivateMemorySize64 / 1MB; $pk = $p.PeakWorkingSet64 / 1MB
        if ($ws -gt $maxWs) { $maxWs = $ws }
        if ($pr -gt $maxPriv) { $maxPriv = $pr }
        if ($pk -gt $peak) { $peak = $pk }
        ("{0:F1};{1:F0};{2:F0};{3:F0}" -f $sw.Elapsed.TotalSeconds, $ws, $pr, $pk) | Out-File -Append -Encoding utf8 $Log
    } catch {}
    Start-Sleep -Milliseconds ([int]($IntervalSec * 1000))
}
$sw.Stop()
"SUMMARY exit={0} wall_s={1:N0} max_ws_mb={2:N0} max_private_mb={3:N0} peak_ws_mb={4:N0}" -f $p.ExitCode, $sw.Elapsed.TotalSeconds, $maxWs, $maxPriv, $peak
