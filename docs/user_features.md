# User features

We want to build a multimodal search tool that helps users find objects,
documents, and archives from the Science Museum Group.

- Users can search the collections using natural language together with filters.
- Users can open a chat panel from the left edge to explore the collection with
  an agent while browsing and searching images in the same view. The frontend
  currently provides a local preview with suggested
  prompts, message history, and a new-chat control. Agent replies require the
  backend connection described in the [frontend guide](../frontend/README.md).
- Many items in the collections are very old. We are considering letting users
  choose an image they like, such as a drawing, and use the Nano Banana model
  to generate a refreshed version.
- Users can use a world model to generate a refreshed view of a collection.
