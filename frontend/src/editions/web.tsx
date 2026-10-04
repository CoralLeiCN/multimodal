import { type AnyRootRoute, createRoute } from "@tanstack/react-router"
import { useState } from "react"
import { ExplorerPanel } from "../components/explorer-panel"
import { CollectionPage } from "../routes/index"

function WebCollection() {
  const [selection, setSelection] = useState<{ text: string; nonce: number }>()
  return (
    <CollectionPage
      sidebar={<ExplorerPanel selection={selection} />}
      onChat={(image) =>
        setSelection({ text: `Explore image ${image.image_id}: ${image.title}`, nonce: Date.now() })
      }
      onRecordChat={(id) =>
        setSelection({ text: `Explore collection record ${id}`, nonce: Date.now() })
      }
    />
  )
}
export function makeRoutes(root: AnyRootRoute) {
  return [createRoute({ getParentRoute: () => root, path: "/", component: WebCollection })]
}
