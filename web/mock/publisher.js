// See publisher.html. Publishes a 512x512 animated canvas as a camera track.
import { Room, Track } from 'livekit-client'

const params = new URLSearchParams(location.search)
const room = params.get('room')
const identity = params.get('identity') ?? 'flashhead-avatar'
const status = document.getElementById('status')
const canvas = document.getElementById('c')
const ctx = canvas.getContext('2d')
// captureStream(0) + requestFrame(): hidden tabs don't composite the canvas, so
// automatic capture would stall when this page is in a background tab.
const stream = canvas.captureStream(0)
const [videoTrack] = stream.getVideoTracks()

function draw() {
  const s = performance.now() / 1000
  const g = ctx.createRadialGradient(256, 200, 20, 256, 256, 360)
  g.addColorStop(0, `hsl(${(s * 40) % 360} 70% 60%)`)
  g.addColorStop(1, '#0b0e14')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, 512, 512)
  // A "face" whose mouth opens and closes.
  ctx.fillStyle = '#f1d7c4'
  ctx.beginPath()
  ctx.ellipse(256, 230, 110, 140, 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = '#222'
  for (const x of [215, 297]) {
    ctx.beginPath()
    ctx.arc(x, 200, 12, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.beginPath()
  ctx.ellipse(256, 300, 40, 6 + 18 * Math.abs(Math.sin(s * 6)), 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = '#fff'
  ctx.font = '600 22px system-ui'
  ctx.textAlign = 'center'
  ctx.fillText(`${identity} · ${s.toFixed(1)}s`, 256, 470)
  videoTrack.requestFrame()
}
// A timer rather than requestAnimationFrame so frames keep coming when this tab is in the background.
setInterval(draw, 40)

async function main() {
  if (!room) throw new Error('missing ?room=')
  const res = await fetch(`/v1/_mock/token?room=${encodeURIComponent(room)}&identity=${encodeURIComponent(identity)}`)
  if (!res.ok) throw new Error(`token HTTP ${res.status}`)
  const { token, livekit_url } = await res.json()
  const lk = new Room()
  await lk.connect(livekit_url, token)
  await lk.localParticipant.publishTrack(videoTrack, { source: Track.Source.Camera, name: 'avatar' })
  status.textContent = `publishing as ${identity} in ${room}`
  window.__published = true
}

main().catch((err) => {
  status.textContent = `error: ${err.message}`
  console.error(err)
})
