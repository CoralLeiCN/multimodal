import { makeRoutes } from "@edition"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createRootRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./styles.css"

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})
const rootRoute = createRootRoute({ component: Outlet })
const router = createRouter({ routeTree: rootRoute.addChildren(makeRoutes(rootRoute)) })

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
