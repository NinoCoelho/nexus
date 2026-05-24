import type { ImportTreeNode } from "../api/vault";

export interface DropEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number;
  children?: DropEntry[];
}

type FSEntry = { isFile: boolean; isDirectory: boolean; name: string };
type FSFileEntry = FSEntry & { file(cb: (f: File) => void): void };
type FSDirReader = { readEntries(cb: (entries: FSEntry[]) => void): void };

const SKIP_NAMES = new Set([".DS_Store", "Thumbs.db", ".git", "__MACOSX", "node_modules"]);

function readDirEntries(reader: FSDirReader): Promise<FSEntry[]> {
  return new Promise((resolve) => {
    const all: FSEntry[] = [];
    const batch = () => {
      reader.readEntries((entries: FSEntry[]) => {
        if (entries.length === 0) resolve(all);
        else { all.push(...entries); batch(); }
      });
    };
    batch();
  });
}

async function readEntry(
  entry: FSEntry,
  parentPath: string,
  filesMap: Map<string, File>,
): Promise<DropEntry | null> {
  if (SKIP_NAMES.has(entry.name)) return null;
  const entryPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;

  if (entry.isFile) {
    const fsFile = entry as unknown as FSFileEntry;
    const file: File = await new Promise<File>((resolve) => { fsFile.file(resolve); });
    filesMap.set(entryPath, file);
    return { name: entry.name, path: entryPath, type: "file", size: file.size };
  }

  if (entry.isDirectory) {
    const dirEntry = entry as unknown as { createReader(): FSDirReader };
    const reader = dirEntry.createReader();
    const children: DropEntry[] = [];
    const childEntries = await readDirEntries(reader);
    for (const child of childEntries) {
      const result = await readEntry(child, entryPath, filesMap);
      if (result) children.push(result);
    }
    return { name: entry.name, path: entryPath, type: "dir", children };
  }

  return null;
}

function getAsEntry(item: DataTransferItem): FSEntry | null {
  if ("webkitGetAsEntry" in item && typeof item.webkitGetAsEntry === "function") {
    return item.webkitGetAsEntry();
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fn = (item as any).webkitGetAsEntry;
  if (typeof fn === "function") return fn.call(item);
  return null;
}

export async function readDropEntries(
  dataTransfer: DataTransfer,
): Promise<{ tree: ImportTreeNode[]; files: Map<string, File> }> {
  const filesMap = new Map<string, File>();
  const entries: DropEntry[] = [];

  // Try the FileSystem API (webkitGetAsEntry) for directory support
  const items = dataTransfer.items;
  if (items && items.length > 0) {
    const promises: Promise<void>[] = [];
    for (let i = 0; i < items.length; i++) {
      const entry = getAsEntry(items[i]);
      if (entry) {
        promises.push(
          readEntry(entry, "", filesMap).then((result) => {
            if (result) entries.push(result);
          }),
        );
      }
    }
    if (promises.length > 0) {
      await Promise.all(promises);
    }
  }

  if (entries.length > 0) {
    return { tree: entries as unknown as ImportTreeNode[], files: filesMap };
  }

  // Fallback: build tree from flat file list (no directory traversal)
  return buildTreeFromFileList(dataTransfer.files);
}

export function buildTreeFromFileList(
  fileList: FileList,
): { tree: ImportTreeNode[]; files: Map<string, File> } {
  const filesMap = new Map<string, File>();
  const dirMap = new Map<string, ImportTreeNode>();

  for (let i = 0; i < fileList.length; i++) {
    const file = fileList[i];
    const relPath = file.webkitRelativePath || file.name;
    if (!relPath) continue;
    if (SKIP_NAMES.has(relPath.split("/").pop() || "")) continue;

    filesMap.set(relPath, file);

    const parts = relPath.split("/");
    for (let d = 1; d < parts.length; d++) {
      const dirPath = parts.slice(0, d).join("/");
      if (!dirMap.has(dirPath)) {
        dirMap.set(dirPath, {
          name: parts[d - 1],
          path: dirPath,
          type: "dir",
          children: [],
        });
      }
    }

    const fileName = parts[parts.length - 1];
    const fileNode: ImportTreeNode = {
      name: fileName,
      path: relPath,
      type: "file",
      size: file.size,
    };

    if (parts.length > 1) {
      const parentPath = parts.slice(0, -1).join("/");
      const parent = dirMap.get(parentPath);
      if (parent) parent.children!.push(fileNode);
    } else {
      dirMap.set(relPath, fileNode);
    }
  }

  // Collect root-level nodes (no "/" in path, or top-level dirs)
  const roots: ImportTreeNode[] = [];
  const topLevelPaths = new Set<string>();
  for (const [path, node] of dirMap) {
    if (node.type === "file" && !path.includes("/")) {
      roots.push(node);
    } else if (node.type === "dir") {
      const topDir = path.split("/")[0];
      if (!topLevelPaths.has(topDir)) {
        topLevelPaths.add(topDir);
        roots.push(dirMap.get(topDir) || node);
      }
    }
  }

  if (roots.length === 0 && filesMap.size > 0) {
    for (const [path] of filesMap) {
      roots.push({
        name: path.split("/").pop() || path,
        path,
        type: "file",
        size: filesMap.get(path)!.size,
      });
    }
  }

  return { tree: roots, files: filesMap };
}

export async function readCsvStats(
  file: File,
  sampleLines = 6,
): Promise<{ headers: string[]; columnCount: number; estimatedRows: number }> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = reader.result as string;
      const lines = text.split("\n").slice(0, sampleLines + 1);
      if (lines.length === 0) {
        resolve({ headers: [], columnCount: 0, estimatedRows: 0 });
        return;
      }
      const headers = lines[0].split(",").map((h) => h.trim().replace(/^"|"$/g, ""));
      const totalLines = text.split("\n").length - 1;
      resolve({ headers, columnCount: headers.length, estimatedRows: Math.max(0, totalLines) });
    };
    reader.onerror = () => resolve({ headers: [], columnCount: 0, estimatedRows: 0 });
    const blob = file.slice(0, 8192);
    reader.readAsText(blob);
  });
}
