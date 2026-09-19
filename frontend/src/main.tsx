import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { CreationStudio } from "./components/creation/studio"
import { CollectionPage } from "./routes/index"
import "./styles.css"

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})
const rootRoute = createRootRoute({ component: Outlet })
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: CollectionPage,
})
const creationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/create",
  component: CreationStudio,
})
const router = createRouter({ routeTree: rootRoute.addChildren([indexRoute, creationRoute]) })

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

const container = document.getElementById("root")
if (container)
  createRoot(container).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  )
