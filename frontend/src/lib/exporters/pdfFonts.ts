/*
 * Fonts for the trip PDF. jsPDF's built-in Helvetica only covers WinAnsi (Latin-1 and
 * a few extras), so lines with other characters switch to an embedded TTF: Caveat for
 * wider Latin (ō, ₹, …), Yomogi for Japanese. Yomogi is 4 MB, so it's fetched and
 * embedded only when the trip has Japanese text.
 */
import type { jsPDF } from 'jspdf';
import caveatRegular from '@expo-google-fonts/caveat/400Regular/Caveat_400Regular.ttf?url';
import caveatBold from '@expo-google-fonts/caveat/700Bold/Caveat_700Bold.ttf?url';
import yomogiRegular from '@expo-google-fonts/yomogi/400Regular/Yomogi_400Regular.ttf?url';

export type PdfFont = 'helvetica' | 'Caveat' | 'Yomogi';

const CJK = /[\p{sc=Han}\p{sc=Hiragana}\p{sc=Katakana}\p{sc=Hangul}]/u;
// WinAnsi: printable ASCII, Latin-1 and the cp1252 extras.
const WIN_ANSI = /^[\x20-\x7e\xa0-\xff€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]*$/;

export const hasCjk = (text: string) => CJK.test(text);
export const fontFor = (text: string): PdfFont => (hasCjk(text) ? 'Yomogi' : WIN_ANSI.test(text) ? 'helvetica' : 'Caveat');

async function base64(url: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('Could not load the PDF fonts. Check your connection and try again.');
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Embed Caveat (regular and bold) and, when needed, Yomogi (also registered as bold, which it lacks). */
export async function embedFonts(pdf: jsPDF, withJapanese: boolean) {
  const files: [string, string, string, 'normal' | 'bold'][] = [
    ['Caveat-Regular.ttf', caveatRegular, 'Caveat', 'normal'],
    ['Caveat-Bold.ttf', caveatBold, 'Caveat', 'bold'],
    ...(withJapanese ? [['Yomogi-Regular.ttf', yomogiRegular, 'Yomogi', 'normal'] as [string, string, string, 'normal']] : []),
  ];
  const data = await Promise.all(files.map(([, url]) => base64(url)));
  files.forEach(([file, , family, style], index) => {
    pdf.addFileToVFS(file, data[index]);
    pdf.addFont(file, family, style);
  });
  if (withJapanese) pdf.addFont('Yomogi-Regular.ttf', 'Yomogi', 'bold');
}
