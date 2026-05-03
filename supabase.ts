import { createClient } from '@supabase/supabase-js';
import dotenv from 'dotenv';

dotenv.config();

const supabaseUrl = process.env.VITE_SUPABASE_URL || '';
const supabaseKey = process.env.VITE_SUPABASE_ANON_KEY || '';

// Create a dummy client if keys are missing to prevent crash on startup
export const supabase = (supabaseUrl && supabaseKey) 
  ? createClient(supabaseUrl, supabaseKey)
  : {
      from: () => ({
        select: () => ({ single: () => Promise.resolve({ data: null, error: new Error('Supabase not configured') }) }),
        upsert: () => Promise.resolve({ error: new Error('Supabase not configured') }),
        insert: () => Promise.resolve({ error: new Error('Supabase not configured') }),
      })
    } as any;

if (!supabaseUrl || !supabaseKey) {
  console.warn('⚠️ Supabase URL or Key is missing! Bot will use local JSON storage fallback.');
}
