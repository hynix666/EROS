import { useEffect, useState } from 'react';

/** JSON-serialized state persisted to localStorage (KnowledgeGraph contract). */
export function useLocalStorageState<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw !== null ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch { /* storage full/blocked — state stays in memory */ }
  }, [key, value]);
  return [value, setValue];
}
