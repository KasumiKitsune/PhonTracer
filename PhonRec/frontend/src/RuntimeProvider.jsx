import { RuntimeContext } from './runtimeContext.js';

export default function RuntimeProvider({ client, children }) {
  return <RuntimeContext.Provider value={client}>{children}</RuntimeContext.Provider>;
}
