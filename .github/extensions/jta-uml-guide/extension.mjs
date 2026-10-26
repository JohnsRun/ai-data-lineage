import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";
import { classes, relations, packages, payrollSequence, mermaid } from "./model.mjs";

const pageUrl = new URL("./page.html", import.meta.url);
const serversByInstance = new Map();

function modelPayload() {
  return {
    classes,
    relations,
    packages,
    sequence: payrollSequence,
    mermaid: { class: mermaid("class"), package: mermaid("package"), sequence: mermaid("sequence") },
  };
}

async function startServer() {
  const server = createServer(async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/") {
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(await readFile(pageUrl));
      } else if (request.method === "GET" && request.url === "/api/model") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify(modelPayload()));
      } else {
        response.writeHead(404).end();
      }
    } catch {
      response.writeHead(500).end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("No TCP address assigned.");
  }
  return { server, url: `http://127.0.0.1:${address.port}/` };
}

const canvas = createCanvas({
  id: "ai-sql-developer-jta-uml-guide",
  displayName: "JTA UML Guide",
  description: "Shows UML class, package and sequence views of the JTA Oracle database with Mermaid output.",
  actions: [
    {
      name: "get-mermaid",
      description: "Return Mermaid source for the class, package or sequence diagram.",
      inputSchema: {
        type: "object",
        properties: { diagram: { type: "string", enum: ["class", "package", "sequence"] } },
        required: ["diagram"],
        additionalProperties: false,
      },
      handler: ({ input }) => {
        const source = mermaid(input?.diagram);
        if (!source) throw new CanvasError("invalid_diagram", "diagram must be class, package or sequence.");
        return { diagram: input.diagram, mermaid: source };
      },
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
      return { status: "ready", title: "JTA UML Guide", url };
    } catch (error) {
      serversByInstance.delete(instanceId);
      throw new CanvasError("uml_canvas_open_failed", `Unable to start the UML guide: ${error.message}`);
    }
  },
  onClose: async ({ instanceId }) => {
    const serverPromise = serversByInstance.get(instanceId);
    if (!serverPromise) return;
    serversByInstance.delete(instanceId);
    const { server } = await serverPromise;
    await new Promise((resolve) => server.close(() => resolve()));
  },
});

await joinSession({ canvases: [canvas] });
