import { useCallback, useEffect, useState } from "react";

export type Locale = "en" | "zh-Hans" | "zh-Hant";

type Dictionary = Record<string, string>;

const EN: Dictionary = {
  "tab.plan": "Plan",
  "tab.saved": "My trips",
  "tab.evaluation": "Evaluation",
  "tab.settings": "Settings",
  "app.tagline": "Agentic trip planner",
  "plan.details": "Trip details",
  "plan.chat": "Chat",
  "plan.generate": "Generate itinerary",
  "plan.save": "Save details",
  "plan.destination": "Destination",
  "plan.from": "From",
  "plan.start": "Start",
  "plan.end": "End",
  "plan.travellers": "Travellers",
  "plan.budget": "Budget",
  "plan.currency": "Currency",
  "plan.hotelStyle": "Hotel style",
  "plan.preferences": "Preferences",
  "plan.constraints": "Hard constraints",
  "plan.emptyTitle": "Start with a trip, not a form",
  "plan.emptyBody": "Tell the assistant where, when, with whom and for how much. It will run four agents and return a sourced itinerary.",
  "plan.emptyAction": "Plan my trip",
  "plan.live": "Live progress",
  "plan.workspace": "Itinerary",
  "plan.map": "Map",
  "chat.placeholder": "Ask for a change, or a quick question…",
  "chat.send": "Send",
  "saved.title": "My trips",
  "saved.body": "Trips you marked useful. A similar new trip can use their shape, never their facts.",
  "saved.empty": "No saved trips yet",
  "eval.title": "Measured, not asserted",
  "eval.body": "Every run is frozen against a dataset, scored with deterministic scripts, and compared with the previous run.",
  "eval.run": "Run evaluation",
  "eval.export": "Export report",
  "settings.title": "Settings",
  "settings.body": "Production thresholds are read-only in the app. Change them in the environment, restart, and record the diff.",
  "error.backend": "The backend is not reachable. Start it with the README command and retry.",
  "common.retry": "Retry",
  "common.loading": "Loading",
};

const ZH_HANS: Dictionary = {
  "tab.plan": "规划",
  "tab.saved": "我的行程",
  "tab.evaluation": "评估",
  "tab.settings": "设置",
  "app.tagline": "智能体行程规划",
  "plan.details": "行程信息",
  "plan.chat": "对话",
  "plan.generate": "生成行程",
  "plan.save": "保存信息",
  "plan.destination": "目的地",
  "plan.from": "出发城市",
  "plan.start": "开始日期",
  "plan.end": "结束日期",
  "plan.travellers": "出行人数",
  "plan.budget": "预算",
  "plan.currency": "币种",
  "plan.hotelStyle": "酒店风格",
  "plan.preferences": "偏好",
  "plan.constraints": "硬性约束",
  "plan.emptyTitle": "先说行程，不必先填表",
  "plan.emptyBody": "告诉助手去哪里、何时去、几个人、预算多少。它会并行运行四个代理，返回一份带来源的行程。",
  "plan.emptyAction": "规划我的行程",
  "plan.live": "实时进度",
  "plan.workspace": "行程",
  "plan.map": "地图",
  "chat.placeholder": "提出修改，或问一个简短问题…",
  "chat.send": "发送",
  "saved.title": "我的行程",
  "saved.body": "你标记为有用的行程。相似的新行程可以借用它们的结构，但不会直接采信旧事实。",
  "saved.empty": "还没有保存的行程",
  "eval.title": "用数据说明，而不是断言",
  "eval.body": "每轮运行都冻结在数据集上，用确定性脚本评分，并与上一轮对比。",
  "eval.run": "运行评估",
  "eval.export": "导出报告",
  "settings.title": "设置",
  "settings.body": "应用内的生产阈值是只读的。请在环境变量中修改，重启，并记录差异。",
  "error.backend": "无法连接后端。请按 README 启动后重试。",
  "common.retry": "重试",
  "common.loading": "加载中",
};

const ZH_HANT: Dictionary = {
  ...ZH_HANS,
  "tab.plan": "規劃",
  "tab.saved": "我的行程",
  "tab.evaluation": "評估",
  "tab.settings": "設定",
  "app.tagline": "智能體行程規劃",
  "plan.details": "行程資訊",
  "plan.chat": "對話",
  "plan.generate": "生成行程",
  "plan.save": "儲存資訊",
  "plan.travellers": "出行人數",
  "plan.hotelStyle": "飯店風格",
  "plan.constraints": "硬性限制",
  "plan.emptyTitle": "先說行程，不必先填表",
  "plan.emptyBody": "告訴助手去哪裡、何時去、幾個人、預算多少。它會並行執行四個代理，回傳一份附來源的行程。",
  "plan.emptyAction": "規劃我的行程",
  "plan.live": "即時進度",
  "plan.workspace": "行程",
  "plan.map": "地圖",
  "chat.placeholder": "提出修改，或問一個簡短問題…",
  "chat.send": "傳送",
  "saved.title": "我的行程",
  "saved.body": "你標記為有用的行程。相似的新行程可以借用它們的結構，但不會直接採信舊事實。",
  "saved.empty": "還沒有儲存的行程",
  "eval.title": "用數據說明，而不是斷言",
  "eval.body": "每輪執行都凍結在資料集上，用確定性腳本評分，並與上一輪比較。",
  "eval.run": "執行評估",
  "eval.export": "匯出報告",
  "settings.title": "設定",
  "settings.body": "應用內的生產閾值是唯讀的。請在環境變數中修改，重新啟動，並記錄差異。",
  "error.backend": "無法連線後端。請按 README 啟動後重試。",
  "common.retry": "重試",
  "common.loading": "載入中",
};

const DICTIONARIES: Record<Locale, Dictionary> = {
  en: EN,
  "zh-Hans": ZH_HANS,
  "zh-Hant": ZH_HANT,
};

const KEY = "pathfinder30.locale";

export function useI18n() {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const stored = localStorage.getItem(KEY);
    return stored === "zh-Hans" || stored === "zh-Hant" ? stored : "en";
  });
  useEffect(() => {
    localStorage.setItem(KEY, locale);
    document.documentElement.lang = locale;
  }, [locale]);
  const setLocale = useCallback((value: string) => {
    if (value in DICTIONARIES) setLocaleState(value as Locale);
  }, []);
  const t = useCallback(
    (key: string, fallback?: string) => DICTIONARIES[locale][key] ?? EN[key] ?? fallback ?? key,
    [locale],
  );
  return { locale, setLocale, t };
}
