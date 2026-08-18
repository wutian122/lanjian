import { Helmet, HelmetProvider } from "react-helmet-async";
import { ReactNode } from "react";

interface PageMetaProps {
  title?: string;
  description?: string;
  keywords?: string;
  image?: string;
  url?: string;
}

interface AppWrapperProps {
  children: ReactNode;
}

export function AppWrapper({ children }: AppWrapperProps) {
  return (
    <HelmetProvider>
      {children}
    </HelmetProvider>
  );
}

export default function PageMeta({
  title = "蓝鉴",
  description = "蓝鉴 - 基于Multi-Agent协作架构的AI代码安全审计平台，提供全面的代码安全检测与漏洞验证",
  keywords = "代码审计,代码质量,AI分析,安全检测,性能优化,代码规范",
  image = "/images/logo.png",
  url = window.location.href
}: PageMetaProps) {
  const fullTitle = title === "蓝鉴" ? title : `${title} - 蓝鉴`;

  return (
    <Helmet>
      {/* 基本信息 */}
      <title>{fullTitle}</title>
      <meta name="description" content={description} />
      <meta name="keywords" content={keywords} />

      {/* Open Graph */}
      <meta property="og:title" content={fullTitle} />
      <meta property="og:description" content={description} />
      <meta property="og:image" content={image} />
      <meta property="og:url" content={url} />
      <meta property="og:type" content="website" />
      <meta property="og:site_name" content="蓝鉴" />

      {/* Twitter Card */}
      <meta name="twitter:card" content="summary_large_image" />
      <meta name="twitter:title" content={fullTitle} />
      <meta name="twitter:description" content={description} />
      <meta name="twitter:image" content={image} />

      {/* 其他 */}
      <meta name="robots" content="index, follow" />
      <meta name="author" content="蓝鉴" />
      <link rel="canonical" href={url} />
    </Helmet>
  );
}