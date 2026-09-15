/// <reference types="vite/client" />

// 这里声明我们自己用到的环境变量，这样在代码里写 import.meta.env.VITE_API_BASE_URL
// 就能获得类型提示，而不是 any。
interface ImportMetaEnv {
  /** 后端 API 的基础地址，例如 http://localhost:8000/api/v1 */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
