/**
 * Draw text with a dark outline so it stays readable on any background.
 * Much cheaper than ctx.shadowBlur, which blurs every glyph on every frame.
 */
export function drawOutlinedText(ctx, text, x, y, color) {
  ctx.lineWidth = 3;
  ctx.lineJoin = 'round';
  ctx.strokeStyle = 'rgba(0, 0, 0, 0.8)';
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}
