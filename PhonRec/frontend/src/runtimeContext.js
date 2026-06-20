import { createContext, useContext } from 'react';

export const RuntimeContext = createContext(null);

export function useRuntimeClient() {
  const client = useContext(RuntimeContext);
  if (!client) {
    throw new Error('PhonRec 运行时客户端尚未初始化');
  }
  return client;
}
