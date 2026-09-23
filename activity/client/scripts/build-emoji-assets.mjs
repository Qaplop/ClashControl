// Build the Discord application-emoji upload files from the icon masters.
//
//   cd activity/client && npm run build:emoji
//
// Reads qapbot/icons/emoji_manifest.json (the same list the bot uploads from) and writes one
// lossless WebP per entry to qapbot/icons/emoji/<name>.webp. Run it whenever a master in
// qapbot/icons/ changes or a manifest entry is added; the output is committed, so the bot never
// converts anything at runtime. See plans/app-emoji-migration.md.
//
// - SVG masters are rasterised at 128x128 (Discord's recommended emoji size).
// - Raster masters are padded to a square, transparent canvas WITHOUT resampling: several are
//   74x100 or 63x100 and upscaling would blur them, while Discord fits any square image anyway.
// - Always lossless: lossy WebP smears the thin edges these small icons are made of.

import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const here = path.dirname(fileURLToPath(import.meta.url))
const iconsDir = path.resolve(here, '../../../qapbot/icons')
const outDir = path.join(iconsDir, 'emoji')
const SVG_SIZE = 128
const MAX_BYTES = 256 * 1024 // Discord's upload limit
const TRANSPARENT = { r: 0, g: 0, b: 0, alpha: 0 }

const manifest = JSON.parse(await readFile(path.join(iconsDir, 'emoji_manifest.json'), 'utf8'))
await mkdir(outDir, { recursive: true })

for (const { name, source } of manifest) {
  const input = path.join(iconsDir, source)
  let image
  if (source.toLowerCase().endsWith('.svg')) {
    // density scales the SVG's own 64-unit viewBox up to the target before rasterising, so edges
    // are rendered at full resolution rather than upscaled from a small bitmap.
    image = sharp(input, { density: 384 }).resize(SVG_SIZE, SVG_SIZE, {
      fit: 'contain',
      background: TRANSPARENT,
    })
  } else {
    const { width, height } = await sharp(input).metadata()
    const side = Math.max(width, height)
    const left = Math.floor((side - width) / 2)
    const top = Math.floor((side - height) / 2)
    image = sharp(input).extend({
      top,
      bottom: side - height - top,
      left,
      right: side - width - left,
      background: TRANSPARENT,
    })
  }

  const output = path.join(outDir, `${name}.webp`)
  const info = await image.webp({ lossless: true, effort: 6 }).toFile(output)
  if (info.size > MAX_BYTES) throw new Error(`${name}: ${info.size} bytes exceeds Discord's 256 KiB limit`)
  if (info.width !== info.height) throw new Error(`${name}: ${info.width}x${info.height} is not square`)
  console.log(`${name.padEnd(12)} ${String(info.width).padStart(3)}x${info.height}  ${info.size} B  <- ${source}`)
}
console.log(`\n${manifest.length} emoji assets written to ${outDir}`)
