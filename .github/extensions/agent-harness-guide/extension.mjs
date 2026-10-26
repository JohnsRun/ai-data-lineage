import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";
import { recommendHarness } from "./recommendation.mjs";

const pageUrl = new URL("./page.html", import.meta.url);
const serversByInstance = new Map();

async function startServer() {
  const server = createServer(async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/") {
        const page = await readFile(pageUrl);
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(page);
        return;
      }

      if (request.method === "POST" && request.url === "/recommend") {
        const input = await readJsonBody(request);
        const result = recommendHarness(input);
        response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        response.end(JSON.stringify(result));
        return;
      }

      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("Not found");
    } catch (error) {
      const status = error instanceof CanvasError ? 400 : 500;
      response.writeHead(status, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ error: error.message }));
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("The canvas server did not receive a TCP address.");
  }

  return { server, url: `http://127.0.0.1:${address.port}/` };
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;

  for await (const chunk of request) {
    size += chunk.length;
    if (size > 4096) {
      throw new CanvasError("invalid_action_input", "Request body is too large.");
    }
    chunks.push(chunk);
  }

  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new CanvasError("invalid_action_input", "Request body must be valid JSON.");
  }
}

const canvas = createCanvas({
  id: "ai-sql-developer-agent-harness-guide",
  displayName: "Agent Harness Guide",
  description: "Compares Copilot, local, and cloud agent harnesses and recommends a fit for a stated goal.",
  actions: [
    {
      name: "recommend-harness",
      description: "Recommend Copilot, local, or cloud based on the user's primary goal.",
      inputSchema: {
        type: "object",
        properties: {
          goal: {
            type: "string",
            enum: ["in_editor", "local_control", "background_work", "data_sensitivity"],
          },
        },
        required: ["goal"],
        additionalProperties: false,
      },
      handler: ({ input }) => recommendHarness(input),
    },
  ],
  open: async ({ instanceId }) => {
    if (typeof instanceId !== "string" || instanceId.length === 0) {
      throw new CanvasError("invalid_instance", "A non-empty canvas instance ID is required.");
    }

    let serverPromise = serversByInstance.get(instanceId);
    if (!serverPromise) {
      serverPromise = startServer();
      serversByInstance.set(instanceId, serverPromise);
    }

    try {
      const { url } = await serverPromise;
      return { status: "ready", title: "Agent Harness Guide", url };
    } catch (error) {
      if (serversByInstance.get(instanceId) === serverPromise) {
        serversByInstance.delete(instanceId);
      }
      throw new CanvasError(
        "agent_harness_canvas_open_failed",
        `Unable to start the Agent Harness Guide: ${error.message}`,
      );
    }
  },
  onClose: async ({ instanceId }) => {
    const serverPromise = serversByInstance.get(instanceId);
    if (!serverPromise) return;

    serversByInstance.delete(instanceId);
    const { server } = await serverPromise;
    await new Promise((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  },
});

await joinSession({ canvases: [canvas] });
