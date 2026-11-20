<#
    Regenerates frontend/media/web/ — the web-sized photography the v2 home
    page imports from.

    `media/` is gitignored, so these derivatives are not committed. Run this
    once after cloning (or after dropping new photos into media/) or the v2
    home page will fail to resolve its image imports.

    Source set: 72.8 MB  ->  web set: 12.0 MB

    Usage:  pwsh -File frontend/scripts/optimize-media.ps1
#>

Add-Type -AssemblyName System.Drawing

$src = Join-Path $PSScriptRoot "..\media" | Resolve-Path
$dst = Join-Path $src "web"
New-Item -ItemType Directory -Force $dst | Out-Null

$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
    Where-Object { $_.MimeType -eq 'image/jpeg' }
$ep = New-Object System.Drawing.Imaging.EncoderParameters(1)
$ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
    [System.Drawing.Imaging.Encoder]::Quality, 82)

$maxWidth = 1800

# hero-bg-transparent*.png keeps its alpha channel and is used as-is.
$files = Get-ChildItem -Path "$src\*" -File -Include *.jpg, *.png |
    Where-Object { $_.Name -notlike "hero-bg-transparent*" }

$before = 0
$after = 0

foreach ($f in $files) {
    try {
        $img = [System.Drawing.Image]::FromFile($f.FullName)
        $before += $f.Length

        $w = $img.Width
        $h = $img.Height
        if ($w -gt $maxWidth) {
            $h = [int]($h * $maxWidth / $w)
            $w = $maxWidth
        }

        $bmp = New-Object System.Drawing.Bitmap($w, $h)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $g.DrawImage($img, 0, 0, $w, $h)
        $g.Dispose()
        $img.Dispose()

        $out = Join-Path $dst ([System.IO.Path]::GetFileNameWithoutExtension($f.Name) + ".jpg")
        $bmp.Save($out, $codec, $ep)
        $bmp.Dispose()

        $after += (Get-Item $out).Length
    }
    catch {
        Write-Warning "Skipped $($f.Name): $_"
    }
}

"Processed {0} files" -f $files.Count
"Before: {0:N1} MB" -f ($before / 1MB)
"After:  {0:N1} MB" -f ($after / 1MB)
