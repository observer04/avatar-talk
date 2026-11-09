import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { VideoTrack, type TrackReference } from '@livekit/components-react'

interface Props {
  trackRef: TrackReference
  /** First frame is on screen. */
  onReady(ready: boolean): void
}

/**
 * Photoreal avatar video. The frame keeps the video's aspect ratio on wide
 * screens and fills the stage (cover-cropped) on narrow ones.
 */
export function VideoStage({ trackRef, onReady }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [aspect, setAspect] = useState(1)
  const sid = trackRef.publication.trackSid

  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    onReady(false)
    const update = () => {
      if (v.videoWidth && v.videoHeight) setAspect(v.videoWidth / v.videoHeight)
    }
    const playing = () => {
      update()
      onReady(true)
    }
    v.addEventListener('resize', update)
    v.addEventListener('playing', playing)
    v.addEventListener('loadeddata', playing)
    if (v.readyState >= 2) playing()
    return () => {
      v.removeEventListener('resize', update)
      v.removeEventListener('playing', playing)
      v.removeEventListener('loadeddata', playing)
    }
    // Re-run per track; onReady is a stable setter from the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid])

  return (
    <div className="video-frame" style={{ '--video-aspect': aspect } as CSSProperties}>
      <VideoTrack ref={videoRef} trackRef={trackRef} className="avatar-video" muted playsInline />
    </div>
  )
}
