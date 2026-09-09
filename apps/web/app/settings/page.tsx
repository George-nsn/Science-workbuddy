import { ModelSettingsWorkbench } from "./workbench";
import { WebSearchSettings } from "./web-search-settings";

export const metadata = { title: "模型与隐私｜Science Buddy" };

export default function SettingsPage() {
  return <><ModelSettingsWorkbench /><WebSearchSettings /></>;
}
