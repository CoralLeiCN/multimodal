import { useQuery } from "@tanstack/react-query"
import { type AnyRootRoute, createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { lazy, Suspense, useState } from "react"
import { api, type Status } from "../components/creation/api"
import { CollectionPage } from "../routes/index"

const Chat = lazy(() => import("../components/chat-panel").then((m) => ({ default: m.ChatPanel })))
function StudioCollection() {
  const enabled = useQuery({ queryKey: ["agent-status"], queryFn: () => api<Status>("/status") })
    .data?.enabled
  const [selection, setSelection] = useState<{ id: string; nonce: number }>()
  const [request, setRequest] = useState<{ recordId: string }>()
  return (
    <CollectionPage
      sidebar={
        enabled && (
          <Suspense fallback={null}>
            <Chat selection={selection} collectionRequest={request} />
          </Suspense>
        )
      }
      headerAction={
        enabled && (
          <a className="header-link" href="/create">
            Image Studio
          </a>
        )
      }
      onChat={
        enabled ? (image) => setSelection({ id: image.image_id, nonce: Date.now() }) : undefined
      }
      onRecordChat={enabled ? (recordId) => setRequest({ recordId }) : undefined}
    />
  )
}
export function makeRoutes(root: AnyRootRoute) {
  return [
    createRoute({ getParentRoute: () => root, path: "/", component: StudioCollection }),
    createRoute({
      getParentRoute: () => root,
      path: "/create",
      component: lazyRouteComponent(
        () => import("../components/creation/studio"),
        "CreationStudio",
      ),
    }),
  ]
}
