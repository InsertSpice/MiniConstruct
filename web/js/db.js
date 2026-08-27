const DB_NAME = "miniconstruct";
const DB_VERSION = 1;

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("projects")) {
        const projects = db.createObjectStore("projects", { keyPath: "id" });
        projects.createIndex("updatedAt", "updatedAt");
      }
      if (!db.objectStoreNames.contains("history")) {
        const history = db.createObjectStore("history", { keyPath: "id", autoIncrement: true });
        history.createIndex("createdAt", "createdAt");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function transact(store, mode, action) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, mode);
    const objectStore = tx.objectStore(store);
    let result;
    try { result = action(objectStore); } catch (error) { reject(error); return; }
    tx.oncomplete = () => resolve(result?.result);
    tx.onerror = () => reject(tx.error);
  }).finally(() => db.close());
}

export async function listProjects() {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction("projects").objectStore("projects").getAll();
    request.onsuccess = () => resolve(request.result.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)));
    request.onerror = () => reject(request.error);
  }).finally(() => db.close());
}

export const getProject = id => transact("projects", "readonly", store => store.get(id));
export const putProject = project => transact("projects", "readwrite", store => store.put(project));
export const deleteProject = id => transact("projects", "readwrite", store => store.delete(id));

export async function addHistory(entry) {
  await transact("history", "readwrite", store => store.add(entry));
  const history = await listHistory();
  for (const stale of history.slice(50)) await transact("history", "readwrite", store => store.delete(stale.id));
}

export async function listHistory() {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db.transaction("history").objectStore("history").getAll();
    request.onsuccess = () => resolve(request.result.sort((a, b) => b.createdAt.localeCompare(a.createdAt)));
    request.onerror = () => reject(request.error);
  }).finally(() => db.close());
}

export const clearHistory = () => transact("history", "readwrite", store => store.clear());

