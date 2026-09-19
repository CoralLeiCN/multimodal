import * as Dialog from "@radix-ui/react-dialog"
import { ArrowUpRight, Copy, MessageCircle, ScanSearch, X } from "lucide-react"
import { useRef, useState } from "react"
import type { ImageRead } from "../client/api"
import { Button } from "./ui/button"

const licenceLinks: Record<string, string> = {
  "CC BY-NC-SA 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
  "CC-BY-NC-SA 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
  "CC BY-NC-ND 4.0": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
  "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
  CC0: "https://creativecommons.org/publicdomain/zero/1.0/",
  "Open Government Licence v3.0":
    "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
}

export function ImageDetail({
  image,
  onClose,
  onSimilar,
  onChat,
  canSearch,
}: {
  image: ImageRead | null
  onClose: () => void
  onSimilar: (image: ImageRead) => void
  onChat: (recordId: string) => void
  canSearch: boolean
}) {
  const pendingChat = useRef<string | null>(null)
  const [copyStatus, setCopyStatus] = useState("")

  async function copyId(recordId: string) {
    try {
      await navigator.clipboard.writeText(recordId)
      setCopyStatus(`Copied ${recordId}`)
    } catch {
      setCopyStatus("Couldn’t copy. Select the collection ID and copy it manually.")
    }
  }

  return (
    <Dialog.Root
      open={Boolean(image)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content
          className="detail-dialog"
          aria-describedby="image-description"
          onCloseAutoFocus={(event) => {
            setCopyStatus("")
            if (pendingChat.current) {
              event.preventDefault()
              onChat(pendingChat.current)
              pendingChat.current = null
            }
          }}
        >
          {image && (
            <>
              <div className="detail-image">
                <img src={image.image_url} alt={image.title} />
              </div>
              <div className="detail-copy">
                <span className="eyebrow">A CLOSER LOOK</span>
                <Dialog.Title>{image.title}</Dialog.Title>
                <Dialog.Description id="image-description">
                  {image.associations[0]?.description ||
                    "Explore this image from the Science Museum Group collection."}
                </Dialog.Description>
                <Button type="button" onClick={() => onSimilar(image)} disabled={!canSearch}>
                  <ScanSearch size={17} /> Find similar images
                </Button>
                {image.associations.map((item) => (
                  <section className="attribution" key={JSON.stringify(item)}>
                    <dl>
                      <div>
                        <dt>Collection ID</dt>
                        <dd className="collection-id">
                          <span>{item.record_uid || "Not supplied"}</span>
                          {item.record_uid && (
                            <span className="collection-id-actions">
                              <Button
                                type="button"
                                variant="ghost"
                                aria-label={`Copy collection ID ${item.record_uid}`}
                                title="Copy collection ID"
                                onClick={() => void copyId(item.record_uid)}
                              >
                                <Copy size={15} aria-hidden="true" />
                              </Button>
                              <Button
                                type="button"
                                variant="ghost"
                                aria-label={`Chat about collection ID ${item.record_uid}`}
                                title="Use collection ID in chat"
                                onClick={() => {
                                  pendingChat.current = item.record_uid
                                  onClose()
                                }}
                              >
                                <MessageCircle size={15} aria-hidden="true" />
                              </Button>
                            </span>
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt>Date</dt>
                        <dd>{item.date || "Not supplied"}</dd>
                      </div>
                      <div>
                        <dt>Place</dt>
                        <dd>{item.places?.join(" · ") || "Not supplied"}</dd>
                      </div>
                      <div>
                        <dt>Category</dt>
                        <dd>{item.categories?.join(" · ") || "Not supplied"}</dd>
                      </div>
                      <div>
                        <dt>Maker</dt>
                        <dd>{item.maker || "Not supplied"}</dd>
                      </div>
                      <div>
                        <dt>Licence</dt>
                        <dd>
                          {licenceLinks[item.licence] ? (
                            <a href={licenceLinks[item.licence]} target="_blank" rel="noreferrer">
                              {item.licence}
                            </a>
                          ) : (
                            item.licence || "Not supplied"
                          )}
                        </dd>
                      </div>
                    </dl>
                    <p className="credit">
                      {item.credit}
                      <br />
                      {item.copyright}
                    </p>
                    <a
                      className="source-link"
                      href={item.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      View collection record <ArrowUpRight size={15} />
                    </a>
                  </section>
                ))}
                <p className="copy-status" role="status">
                  {copyStatus}
                </p>
              </div>
            </>
          )}
          <Dialog.Close className="dialog-close" aria-label="Close image details">
            <X size={22} />
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
