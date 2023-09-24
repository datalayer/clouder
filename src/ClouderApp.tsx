import { createRoot } from 'react-dom/client';
import Clouder from './Clouder';

const div = document.createElement('div');
document.body.appendChild(div);
const root = createRoot(div);

root.render(<Clouder />);
