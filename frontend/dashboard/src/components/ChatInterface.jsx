import React, { useState, useRef, useEffect } from 'react'
import { Send, Bot, User, ChevronDown, Sparkles, BookOpen, FileText } from 'lucide-react'
import clsx from 'clsx'
import { queryRAG } from '../services/api'
import DemoBadge from './DemoBadge'

const SUGGESTED_QUESTIONS = [
  'What are the safety protocols?',
  'How do I calibrate sensors?',
  'Show maintenance schedule',
  'Explain temperature anomalies',
]

const WELCOME_MESSAGE = {
  role: 'assistant',
  content:
    "Hello! I'm the NeuraFleet AI Assistant. I can help you with questions about your robot fleet, maintenance procedures, sensor calibration, safety protocols, and more. You can also scope your questions to a specific robot by selecting one from the dropdown above.\n\nTry one of the suggested questions below, or ask me anything about fleet operations.",
  sources: [],
}

function formatTimestamp(date) {
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  })
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2 px-4 py-3 max-w-xs">
      <div className="flex items-center gap-1.5 bg-slate-700/50 rounded-2xl rounded-bl-md px-4 py-3">
        <div className="w-2 h-2 rounded-full bg-slate-400 typing-dot" />
        <div className="w-2 h-2 rounded-full bg-slate-400 typing-dot" />
        <div className="w-2 h-2 rounded-full bg-slate-400 typing-dot" />
      </div>
    </div>
  )
}

function MessageBubble({ message }) {
  const isUser = message.role === 'user'
  return (
    <div className={clsx('flex animate-slide-up', isUser ? 'justify-end' : 'justify-start')}>
      <div className={clsx('flex gap-2.5 max-w-[80%]', isUser && 'flex-row-reverse')}>
        {/* Avatar */}
        <div
          className={clsx(
            'w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-1',
            isUser ? 'bg-primary-600' : 'bg-slate-700',
          )}
        >
          {isUser ? (
            <User size={14} className="text-white" />
          ) : (
            <Bot size={14} className="text-cyan-400" />
          )}
        </div>

        {/* Bubble */}
        <div>
          <div
            className={clsx(
              'rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
              isUser
                ? 'bg-primary-600 text-white rounded-br-md'
                : 'bg-slate-700/50 text-slate-200 rounded-bl-md border border-slate-700/50',
            )}
          >
            {message.content.split('\n').map((line, i) => (
              <p key={i} className={i > 0 ? 'mt-2' : ''}>
                {line}
              </p>
            ))}
          </div>

          {message.demo && <DemoBadge className="mt-2" />}

          {/* Sources */}
          {message.sources && message.sources.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {message.sources.map((source, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 text-[10px] bg-slate-800 border border-slate-700 text-slate-400 px-2 py-0.5 rounded-full"
                >
                  <FileText size={9} />
                  {source}
                </span>
              ))}
            </div>
          )}

          {/* Timestamp */}
          {message.timestamp && (
            <p
              className={clsx(
                'text-[10px] text-slate-600 mt-1',
                isUser ? 'text-right' : 'text-left',
              )}
            >
              {formatTimestamp(message.timestamp)}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ChatInterface({ fleet, selectedRobot }) {
  const [messages, setMessages] = useState([WELCOME_MESSAGE])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [chatRobotId, setChatRobotId] = useState(selectedRobot?.robot_id || '')
  const [showRobotDropdown, setShowRobotDropdown] = useState(false)
  const [error, setError] = useState(null)

  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (selectedRobot?.robot_id) {
      setChatRobotId(selectedRobot.robot_id)
    }
  }, [selectedRobot])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return

    const userMsg = {
      role: 'user',
      content: text,
      timestamp: new Date(),
    }

    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setLoading(true)
    setError(null)

    try {
      const response = await queryRAG(text, chatRobotId || null)
      const assistantMsg = {
        role: 'assistant',
        content: response.response,
        sources: response.sources || [],
        demo: Boolean(response.demo),
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch {
      setError('Failed to get a response. Please try again.')
      const errMsg = {
        role: 'assistant',
        content:
          'I apologize, but I encountered an error processing your request. Please try again or check if the backend service is running.',
        sources: [],
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, errMsg])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSuggestion = (question) => {
    setInput(question)
    inputRef.current?.focus()
  }

  const selectedChatRobot = fleet.find((r) => r.robot_id === chatRobotId)

  return (
    <div className="h-full flex flex-col animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-primary-600/10 border border-primary-500/20">
            <Sparkles size={16} className="text-primary-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">Fleet AI Assistant</h3>
            <p className="text-[11px] text-slate-500">
              Powered by RAG - Ask about fleet operations
            </p>
          </div>
        </div>

        {/* Robot Context Selector */}
        <div className="relative">
          <button
            onClick={() => setShowRobotDropdown(!showRobotDropdown)}
            className="flex items-center gap-2 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-300 hover:border-slate-600 transition-colors"
          >
            <Bot size={12} className="text-slate-400" />
            <span>{selectedChatRobot?.name || 'All Robots'}</span>
            <ChevronDown
              size={12}
              className={clsx(
                'text-slate-400 transition-transform',
                showRobotDropdown && 'rotate-180',
              )}
            />
          </button>
          {showRobotDropdown && (
            <div className="absolute top-full right-0 mt-1 w-48 bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 py-1 animate-fade-in max-h-60 overflow-y-auto">
              <button
                onClick={() => {
                  setChatRobotId('')
                  setShowRobotDropdown(false)
                }}
                className={clsx(
                  'w-full text-left px-3 py-1.5 text-xs hover:bg-slate-700/50 transition-colors',
                  !chatRobotId ? 'text-primary-400' : 'text-slate-300',
                )}
              >
                All Robots
              </button>
              {fleet.map((robot) => (
                <button
                  key={robot.robot_id}
                  onClick={() => {
                    setChatRobotId(robot.robot_id)
                    setShowRobotDropdown(false)
                  }}
                  className={clsx(
                    'w-full text-left px-3 py-1.5 text-xs hover:bg-slate-700/50 transition-colors',
                    chatRobotId === robot.robot_id ? 'text-primary-400' : 'text-slate-300',
                  )}
                >
                  {robot.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Messages Area */}
      <div className="flex-1 glass-panel overflow-hidden flex flex-col">
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg, i) => (
            <MessageBubble key={i} message={msg} />
          ))}
          {loading && <TypingIndicator />}

          {/* Suggested questions (show after welcome only) */}
          {messages.length === 1 && !loading && (
            <div className="flex flex-wrap gap-2 pt-2 animate-slide-up">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => handleSuggestion(q)}
                  className="inline-flex items-center gap-1.5 text-xs bg-slate-800/50 border border-slate-700/50 text-slate-400
                             hover:text-primary-400 hover:border-primary-500/30 hover:bg-primary-600/5
                             px-3 py-1.5 rounded-full transition-all duration-150"
                >
                  <BookOpen size={11} />
                  {q}
                </button>
              ))}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Error */}
        {error && (
          <div className="mx-4 mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-400">
            {error}
          </div>
        )}

        {/* Input Area */}
        <div className="border-t border-slate-700/50 p-3">
          <div className="flex items-end gap-2">
            <div className="flex-1 relative">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  chatRobotId
                    ? `Ask about ${selectedChatRobot?.name || 'this robot'}...`
                    : 'Ask about your fleet...'
                }
                rows={1}
                className="w-full bg-slate-800/50 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white
                           placeholder-slate-500 focus:outline-none focus:border-primary-500/50 focus:ring-1 focus:ring-primary-500/20
                           resize-none max-h-32 transition-colors"
                style={{
                  height: 'auto',
                  minHeight: '40px',
                }}
                onInput={(e) => {
                  e.target.style.height = 'auto'
                  e.target.style.height = `${Math.min(e.target.scrollHeight, 128)}px`
                }}
                disabled={loading}
              />
            </div>
            <button
              onClick={handleSend}
              disabled={!input.trim() || loading}
              className="btn-primary p-2.5 rounded-xl"
            >
              <Send size={16} />
            </button>
          </div>
          <p className="text-[10px] text-slate-600 mt-1.5 px-1">
            Press Enter to send. AI responses are generated using fleet documentation.
          </p>
        </div>
      </div>
    </div>
  )
}
