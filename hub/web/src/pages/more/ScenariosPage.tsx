import { useTranslation } from "react-i18next";
import { EmptyState } from "../../components/EmptyState";

export default function ScenariosPage() {
  const { t } = useTranslation("common");
  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">{t("more.scenarios")}</h1>
      <EmptyState message="Буде реалізовано в S2" icon="⏱" />
    </div>
  );
}
