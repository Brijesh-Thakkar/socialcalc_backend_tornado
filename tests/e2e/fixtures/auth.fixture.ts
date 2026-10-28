import { test as base } from '@playwright/test';
import { generateUniqueUser, registerUser, loginUser } from '../helpers/app-helper';

type AuthFixtures = {
  // A test user structure generated dynamically
  testUser: { email: string; password: string };
  // A page that is already logged in with a dynamically registered test user
  authenticatedPage: any;
};

export const test = base.extend<AuthFixtures>({
  testUser: async ({}, use) => {
    const user = generateUniqueUser();
    await use(user);
  },
  
  authenticatedPage: async ({ page, testUser }, use) => {
    // 1. Register a unique user
    await registerUser(page, testUser.email, testUser.password);
    
    // 2. Log in with the registered user's credentials
    await loginUser(page, testUser.email, testUser.password);
    
    // 3. Hand over the logged-in page context to the test
    await use(page);
  },
});

export { expect } from '@playwright/test';
