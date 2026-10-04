import { type AnyRootRoute, createRoute } from "@tanstack/react-router"
import { CollectionPage } from "../routes/index"
export function makeRoutes(root: AnyRootRoute) {
  return [createRoute({ getParentRoute: () => root, path: "/", component: CollectionPage })]
}
