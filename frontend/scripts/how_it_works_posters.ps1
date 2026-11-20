Add-Type -AssemblyName System.Drawing

$W = 1200
$H = 1500
$shots = 'C:\Users\Dies Irae\AppData\Local\Temp\cursor\screenshots'
$art = 'C:\Users\Dies Irae\.cursor\projects\c-Users-Dies-Irae-Desktop-HKU-LLM\assets'
$out = 'C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder_LangGraph_Version_Beta_16_11\frontend\public\home\how-it-works'
function FontFile([string]$path, [float]$size, [System.Drawing.FontStyle]$style) {
  $bag = New-Object System.Drawing.Text.PrivateFontCollection
  $bag.AddFontFile($path)
  return New-Object System.Drawing.Font($bag.Families[0], $size, $style, [System.Drawing.GraphicsUnit]::Pixel)
}

function Cover([string]$path, [int]$w, [int]$h) {
  $src = [System.Drawing.Image]::FromFile($path)
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = 'HighQualityBicubic'
  $g.PixelOffsetMode = 'HighQuality'
  $g.SmoothingMode = 'HighQuality'
  $scale = [Math]::Max($w / $src.Width, $h / $src.Height)
  $nw = [int]($src.Width * $scale)
  $nh = [int]($src.Height * $scale)
  $g.DrawImage($src, [int](($w - $nw) / 2), [int](($h - $nh) / 2), $nw, $nh)
  $g.Dispose(); $src.Dispose()
  return $bmp
}

function Round([System.Drawing.Bitmap]$src, [int]$radius) {
  $bmp = New-Object System.Drawing.Bitmap $src.Width, $src.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = 'HighQuality'
  $g.InterpolationMode = 'HighQualityBicubic'
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $d = $radius * 2
  $r = New-Object System.Drawing.Rectangle 0, 0, $src.Width, $src.Height
  $path.AddArc(0, 0, $d, $d, 180, 90)
  $path.AddArc($src.Width - $d, 0, $d, $d, 270, 90)
  $path.AddArc($src.Width - $d, $src.Height - $d, $d, $d, 0, 90)
  $path.AddArc(0, $src.Height - $d, $d, $d, 90, 90)
  $path.CloseFigure()
  $g.SetClip($path)
  $g.DrawImage($src, 0, 0)
  $g.Dispose(); $path.Dispose()
  return $bmp
}

$posters = @(
  @{ file='destination.png'; bg="$art\bg-destination.jpg"; shot="$shots\pf-welcome.png"; ink='#111111'; muted='#464440'; kicker='01   HOW IT WORKS'; title='Say where'; title2="you're going."; caption='A place, or just a mood. Then three quick questions.' },
  @{ file='build-together.png'; bg="$art\bg-build.jpg"; shot="$shots\pf-choice.png"; ink='#111111'; muted='#37465A'; kicker='02   HOW IT WORKS'; title='A full draft,'; title2='or one day at a time.'; caption='Generate the itinerary, or build it with your guide.' },
  @{ file='trip-studio.png'; bg="$art\bg-studio.jpg"; shot="$shots\pf-schedule.png"; ink='#F7F6F3'; muted='#D2CEC6'; kicker='03   HOW IT WORKS'; title='Open the'; title2='Trip Studio.'; caption='A timed day plan, a sketchbook, a map and a 3D route.' },
  @{ file='take-with-you.png'; bg="$art\bg-export.jpg"; shot="$shots\pf-export.png"; ink='#1C241C'; muted='#374637'; kicker='04   HOW IT WORKS'; title='Take the plan'; title2='with you.'; caption='PDF, calendar, map files and a budget spreadsheet.' }
)

foreach ($p in $posters) {
  $canvas = Cover $p.bg $W $H
  $g = [System.Drawing.Graphics]::FromImage($canvas)
  $g.TextRenderingHint = 'AntiAliasGridFit'
  $ink = [System.Drawing.ColorTranslator]::FromHtml($p.ink)
  $muted = [System.Drawing.ColorTranslator]::FromHtml($p.muted)
  $kickerFont = FontFile 'C:\Windows\Fonts\segoeui.ttf' 18 Regular
  $titleFont = FontFile 'C:\Windows\Fonts\georgia.ttf' 64 Regular
  $italicFont = FontFile 'C:\Windows\Fonts\georgiai.ttf' 64 Italic
  $captionFont = FontFile 'C:\Windows\Fonts\segoeui.ttf' 20 Regular
  $g.DrawString($p.kicker, $kickerFont, (New-Object System.Drawing.SolidBrush $muted), 72, 78)
  $g.DrawString($p.title, $titleFont, (New-Object System.Drawing.SolidBrush $ink), 68, 118)
  $g.DrawString($p.title2, $italicFont, (New-Object System.Drawing.SolidBrush $ink), 68, 200)
  $g.DrawString($p.caption, $captionFont, (New-Object System.Drawing.SolidBrush $muted), 72, 292)

  $shot = [System.Drawing.Image]::FromFile($p.shot)
  $cardW = 1040
  $cardH = [int]($cardW * $shot.Height / $shot.Width)
  if ($cardH -gt 900) { $cardH = 900 }
  $fitted = Cover $p.shot $cardW $cardH
  $card = Round $fitted 26
  $x = [int](($W - $cardW) / 2)
  $y = 400
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(40, 0, 0, 0))), ($x + 8), ($y + 18), $cardW, $cardH)
  $g.DrawImage($card, $x, $y)
  $dest = Join-Path $out $p.file
  $canvas.Save($dest, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $canvas.Dispose(); $shot.Dispose(); $fitted.Dispose(); $card.Dispose()
  Write-Output "$($p.file) $((Get-Item $dest).Length)"
}
