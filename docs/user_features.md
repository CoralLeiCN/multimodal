# User features

We want to build a multimodal search tool that helps users find objects,
documents, and archives from the Science Museum Group.

- Users can search the collections using natural language together with filters.
- The `/create` page saves six brand attributes: name, description, colors palette,
  personality, typography, and illustration style. Designers generate images in
  the collection chat using the saved brand. Prompt text is editable in YAML;
  see [brand prompts](brand_prompts.md).
- The chat backend accepts collection image IDs in messages and can generate in the
  configured brand style, then edit the result in later turns. The collection sidebar uses the chat API.
- Users can open a chat panel from the left edge to explore the collection with
  an agent while browsing and searching images in the same view. The frontend
  connects to saved brand conversations, with task status, result images,
  history, and a new-chat control. See the [frontend guide](../frontend/README.md).
- Users can use a world model to generate a refreshed view of a collection.


The conversation backend supports brand-bound chats, collection image IDs, and
follow-up edits in Modal sandboxes. See [Brand chat agent backend](chat_agent_backend.md)
for endpoints, configuration, recovery, and verification. The collection chat sidebar connects to these endpoints and supports saved
conversations, task cancellation, image downloads, and follow-up edits.


Gemini Flash handles chat and compiles image tasks in the trusted backend. Modal
sandboxes execute source lookup, Nano Banana generation, evaluation, and result return.
Current user instructions override conflicting brand defaults for that task; the
saved brand remains unchanged. Pure chat does not create a sandbox. Collection image
IDs can resolve through the local catalogue or a configured trusted online API.
