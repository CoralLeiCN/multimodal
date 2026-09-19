import { ArrowUpRight, ImageOff, ScanSearch } from "lucide-react"
import { useState } from "react"
import type { ImageRead } from "../client/api"

export function ImageCard({
  image,
  onOpen,
  onSimilar,
  onChat,
  canSearch,
}: {
  image: ImageRead
  onOpen: (image: ImageRead) => void
  onSimilar: (image: ImageRead) => void
  onChat: (image: ImageRead) => void
  canSearch: boolean
}) {
  const [failed, setFailed] = useState(false)
  const association = image.associations[0]
  return (
    <article className="image-card" data-testid="image-card">
      <button
        type="button"
        className="image-open"
        onClick={() => onOpen(image)}
        aria-label={`View ${image.title}`}
      >
        <div className="image-stage">
          {failed ? (
            <span className="missing-image">
              <ImageOff size={24} />
              Image unavailable
            </span>
          ) : (
            <img
              src={image.image_url}
              alt={image.title}
              loading="lazy"
              onError={() => setFailed(true)}
            />
          )}
          <span className="view-image">
            <ArrowUpRight size={17} />
          </span>
        </div>
        <div className="card-copy">
          <span className="card-date">{association?.date || "FROM THE COLLECTION"}</span>
          <h3>{image.title}</h3>
        </div>
      </button>
      <button
        type="button"
        className="image-chat-link"
        onClick={() => onChat(image)}
        aria-label={`Use ${image.title} in chat`}
      >
        Use in chat
      </button>
      <div className="card-bottom">
        <span title={association?.licence}>
          {association?.licence || "Rights information unavailable"}
        </span>
        <button
          type="button"
          onClick={() => onSimilar(image)}
          disabled={!canSearch}
          aria-label={`Find images similar to ${image.title}`}
          title="Find similar"
        >
          <ScanSearch size={16} />
          <span>Similar</span>
        </button>
      </div>
    </article>
  )
}
