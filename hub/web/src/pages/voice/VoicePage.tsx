import { useTranslation } from "react-i18next";
import { EmptyState } from "../../components/EmptyState";

export default function VoicePage() {
  const { t } = useTranslation("voice");
  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">{t("title")}</h1>
      <EmptyState message="Буде реалізовано в S2" icon="◎" />
    </div>
  );
}
