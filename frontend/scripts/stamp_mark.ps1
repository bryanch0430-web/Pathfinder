Add-Type -AssemblyName System.Drawing
$code = @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.IO;

public static class MarkStamp {
  public static int Search(string imagePath, int tx, int ty, int tw, int th, int limit, bool write, string inkPath) {
    var src = new Bitmap(imagePath);
    var bmp = new Bitmap(src.Width, src.Height, PixelFormat.Format24bppRgb);
    using (var g = Graphics.FromImage(bmp)) g.DrawImage(src, 0, 0, src.Width, src.Height);
    src.Dispose();
    var data = bmp.LockBits(new Rectangle(0, 0, bmp.Width, bmp.Height), ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
    int stride = data.Stride;
    byte[] pix = new byte[stride * bmp.Height];
    Marshal.Copy(data.Scan0, pix, 0, pix.Length);
    bmp.UnlockBits(data);
    byte[] tpl = new byte[tw * th];
    for (int y = 0; y < th; y++) for (int x = 0; x < tw; x++) tpl[y * tw + x] = pix[(ty + y) * stride + (tx + x) * 3];
    var hits = new List<int[]>();
    int best = int.MaxValue; int bx = 0, by = 0;
    for (int y = 0; y <= bmp.Height - th; y += 2) {
      for (int x = 0; x <= bmp.Width - tw; x += 2) {
        int sad = 0;
        for (int j = 0; j < th && sad <= limit; j++) {
          int row = (y + j) * stride + x * 3;
          int trow = j * tw;
          for (int i = 0; i < tw; i++) {
            int d = pix[row + i * 3] - tpl[trow + i];
            if (d < 0) d = -d;
            sad += d;
          }
        }
        if (sad < best) { best = sad; bx = x; by = y; }
        if (sad <= limit) hits.Add(new int[] { x, y, sad });
      }
    }
    var kept = new List<int[]>();
    hits.Sort((a, b) => a[2].CompareTo(b[2]));
    foreach (var h in hits) {
      bool near = false;
      foreach (var k in kept) if (Math.Abs(k[0] - h[0]) < 16 && Math.Abs(k[1] - h[1]) < 16) near = true;
      if (!near) kept.Add(h);
    }
    Console.WriteLine(Path.GetFileName(imagePath) + " best=" + best + " at " + bx + "," + by + " kept=" + kept.Count);
    if (write && kept.Count > 0 && kept.Count <= 6) {
      var ink = new Bitmap(inkPath);
      using (var g = Graphics.FromImage(bmp)) {
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.CompositingMode = CompositingMode.SourceOver;
        foreach (var h in kept) {
          var bg = bmp.GetPixel(Math.Max(0, h[0] - 3), h[1]);
          using (var brush = new SolidBrush(bg)) g.FillRectangle(brush, h[0] - 3, h[1] - 6, tw + 8, th + 12);
          g.DrawImage(ink, new Rectangle(h[0] - 4, h[1] - 8, 22, 22), new Rectangle(0, 0, ink.Width, ink.Height), GraphicsUnit.Pixel);
        }
      }
      ink.Dispose();
      bmp.Save(imagePath, ImageFormat.Png);
    }
    bmp.Dispose();
    return kept.Count;
  }
}
'@
Add-Type -TypeDefinition $code -ReferencedAssemblies System.Drawing
$root = "C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder_LangGraph_Version_Beta_16_11\frontend\public"
$ink = "$root\brand\mark-ink.png"
$files = Get-ChildItem "$root\guide","$root\home" -Recurse -Filter *.png
foreach ($file in $files) {
  [MarkStamp]::Search($file.FullName, 50, 26, 22, 18, 900, $true, $ink) | Out-Null
}
