import type { Metadata } from "next";

import { DailyWorkbenchEditor } from "./daily-workbench-editor";

export const metadata: Metadata = { title: "每日科研笔记｜Science Buddy" };

export default async function WorkbenchDayPage({
  params,
}: Readonly<{ params: Promise<{ date: string }> }>) {
  const { date } = await params;
  return <DailyWorkbenchEditor entryDate={date} />;
}
