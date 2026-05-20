import { WifiOff } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";

export function OfflineBanner() {
  const isOnline = useOnlineStatus();
  const { t } = useTranslation("common");

  if (isOnline) return null;

  return (
    <div className="flex items-center justify-center gap-2 bg-warm-600 text-white text-xs font-medium py-2 px-4 z-50">
      <WifiOff size={13} />
      <span>{t("status.offline_banner")}</span>
    </div>
  );
}
