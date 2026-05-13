import { useTranslation } from "react-i18next";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";

export function OfflineBanner() {
  const isOnline = useOnlineStatus();
  const { t } = useTranslation("common");

  if (isOnline) return null;

  return (
    <div className="bg-amber-700 text-white text-center text-sm py-1.5 px-4 z-50">
      {t("status.offline_banner")}
    </div>
  );
}
