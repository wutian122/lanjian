import { createContext, useContext, useState, ReactNode } from 'react';

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

interface ChatContextType {
  messages: ChatMessage[];
  draft: string;
  sending: boolean;
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  setDraft: (draft: string) => void;
  setSending: (sending: boolean) => void;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

export const ChatProvider = ({ children }: { children: ReactNode }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  return (
    <ChatContext.Provider value={{ messages, draft, sending, setMessages, setDraft, setSending }}>
      {children}
    </ChatContext.Provider>
  );
};

export const useChat = () => {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error('useChat must be used within a ChatProvider');
  }
  return context;
};
