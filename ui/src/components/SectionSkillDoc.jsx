import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function SectionSkillDoc({ markdown }) {
  return (
    <div className="section-skill-doc">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
    </div>
  )
}
